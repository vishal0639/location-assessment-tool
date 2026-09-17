import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  api,
  fmtDate,
  isInProgress,
  SOURCE_NAMES,
  type Detail,
  type Override,
  type RunDetail,
  type Verdict,
} from "../api";
import { ScoreText, StatusBadge, VerdictBadge } from "../components";

const POLL_MS = 2000;

export default function DetailPage() {
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const runId = params.get("run") ? Number(params.get("run")) : undefined;
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await api.get(id, runId));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id, runId]);

  useEffect(() => {
    load();
  }, [load]);

  const latest = detail?.runs[0];
  const inProgress = isInProgress(latest?.status) || isInProgress(detail?.run?.status);
  useEffect(() => {
    if (!inProgress) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [inProgress, load]);

  if (error) return <p className="error">Could not load assessment: {error}</p>;
  if (!detail) return <p className="muted">Loading…</p>;

  const run = detail.run;
  const viewingOld = run !== null && run.id !== detail.latest_run_id;

  const rerun = async () => {
    try {
      await api.rerun(id);
      setParams({});
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <>
      <h1>{detail.label}</h1>
      <p className="muted">
        Submitted {fmtDate(detail.created_at)} ·{" "}
        {detail.input_address ?? `${detail.input_lat}, ${detail.input_lon}`}
        {run?.matched_address && <> · matched: {run.matched_address}</>}
        {run?.lat != null && <> · resolved to {run.lat.toFixed(5)}, {run.lon!.toFixed(5)}</>}
      </p>

      <div className="row">
        <label>
          Run{" "}
          <select
            value={run?.id ?? ""}
            onChange={(e) => setParams(Number(e.target.value) === detail.latest_run_id ? {} : { run: e.target.value })}
          >
            {detail.runs.map((r) => (
              <option key={r.id} value={r.id}>
                #{r.id} · {fmtDate(r.created_at)} · {r.status}
                {r.id === detail.latest_run_id ? " (latest)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button onClick={rerun} disabled={isInProgress(latest?.status)}>
          Re-run enrichment
        </button>
        <span className="muted small">Re-running adds a new run; earlier runs are kept.</span>
      </div>

      {viewingOld && (
        <p className="banner info">
          You are viewing an older run (#{run!.id}). The latest run is #{detail.latest_run_id}.
        </p>
      )}

      {run && <RunBanner run={run} />}

      <div className="panels">
        <section className="card">
          <h3>Machine score (run #{run?.id})</h3>
          {run && !isInProgress(run.status) && run.status !== "failed" ? (
            <>
              <p className="big">
                <ScoreText score={run.score} status={run.status} />
                {run.score !== null && <span className="muted"> / 100</span>}
              </p>
              <p>
                Verdict: <VerdictBadge verdict={run.machine_verdict} />
              </p>
              <p className="small">{run.verdict_reason}</p>
              <p className="muted small">
                Based on {run.points_available} of {run.points_possible} weighted points being available ·
                scoring rules {run.scoring_version}
              </p>
            </>
          ) : (
            <p className="muted">{run?.status === "failed" ? "Not scored (run failed)." : "Waiting for enrichment…"}</p>
          )}
        </section>

        <section className="card">
          <h3>Analyst verdict</h3>
          {detail.current_override ? (
            <OverrideView o={detail.current_override} latestRunId={detail.latest_run_id} />
          ) : (
            <p className="muted">No override. The machine verdict stands.</p>
          )}
          <OverrideForm id={id} runId={run?.id} onSaved={load} />
        </section>
      </div>

      {run && run.factors.length > 0 && <FactorsTable run={run} />}
      {run && run.fetches.length > 0 && <FetchesTable run={run} />}

      {detail.overrides.length > 1 && (
        <section>
          <h3>Override history</h3>
          <ul className="history">
            {detail.overrides.map((o) => (
              <li key={o.id}>
                <OverrideView o={o} latestRunId={detail.latest_run_id} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function RunBanner({ run }: { run: RunDetail }) {
  if (isInProgress(run.status)) {
    return (
      <p className="banner info">
        <StatusBadge status={run.status} /> Enrichment is running in the background. This page updates on its own.
      </p>
    );
  }
  if (run.status === "failed") {
    return (
      <p className="banner bad">
        <strong>Run failed — no score.</strong> {run.error}
      </p>
    );
  }
  const missing = run.factors.filter((f) => f.status === "unavailable");
  if (missing.length > 0) {
    return (
      <div className="banner warn">
        <strong>
          Partial report: {missing.length} of {run.factors.length} factors unavailable.
        </strong>{" "}
        The score below uses only the factors that could be fetched; missing factors are excluded, not counted as zero.
        <ul>
          {missing.map((f) => (
            <li key={f.factor}>
              {f.label}: {f.explanation.replace(/^Unavailable - /, "")}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return <p className="banner good">Complete: all {run.factors.length} factors were available.</p>;
}

function FactorsTable({ run }: { run: RunDetail }) {
  return (
    <section>
      <h3>Factors</h3>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Factor</th>
              <th>Derived value</th>
              <th>Raw value</th>
              <th>Points</th>
              <th>Rule applied</th>
              <th>Source</th>
              <th>Fetched at</th>
            </tr>
          </thead>
          <tbody>
            {run.factors.map((f) => (
              <tr key={f.factor} className={f.status === "unavailable" ? "unavailable" : ""}>
                <td>{f.label}</td>
                <td>{f.status === "unavailable" ? <strong>UNAVAILABLE</strong> : f.derived_value}</td>
                <td><code>{f.raw_value === null ? "—" : JSON.stringify(f.raw_value)}</code></td>
                <td>
                  {f.points === null ? <span className="muted">not counted</span> : `${f.points} / ${f.max_points}`}
                </td>
                <td className="small">{f.explanation}</td>
                <td>{SOURCE_NAMES[f.source] ?? f.source}</td>
                <td className="small">{fmtDate(f.fetched_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FetchesTable({ run }: { run: RunDetail }) {
  return (
    <section>
      <h3>Source calls</h3>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Result</th>
              <th>HTTP</th>
              <th>Took</th>
              <th>Fetched at</th>
              <th>Raw response</th>
            </tr>
          </thead>
          <tbody>
            {run.fetches.map((f) => (
              <tr key={f.id} className={f.status === "error" ? "unavailable" : ""}>
                <td>
                  {SOURCE_NAMES[f.source] ?? f.source}
                  <div className="small"><a href={f.request_url} target="_blank" rel="noreferrer">request URL</a></div>
                </td>
                <td>
                  {f.status === "ok" ? "OK" : <><strong>{f.error_kind}</strong><div className="small">{f.error_detail}</div></>}
                </td>
                <td>{f.http_status ?? "—"}</td>
                <td>{(f.duration_ms / 1000).toFixed(1)}s</td>
                <td className="small">{fmtDate(f.fetched_at)}</td>
                <td>
                  {f.raw_payload === null ? (
                    <span className="muted">none</span>
                  ) : (
                    <details>
                      <summary>show</summary>
                      <pre>{JSON.stringify(f.raw_payload, null, 2).slice(0, 5000)}</pre>
                    </details>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function OverrideView({ o, latestRunId }: { o: Override; latestRunId: number | null }) {
  return (
    <div>
      <p>
        <VerdictBadge verdict={o.verdict} /> by <strong>{o.analyst}</strong> on {fmtDate(o.created_at)}
      </p>
      <blockquote>{o.reason}</blockquote>
      <p className="muted small">
        Made against run #{o.run_id} (machine: score {o.run_score ?? "none"}, verdict {o.run_machine_verdict ?? "none"})
        {o.run_id !== latestRunId && " — a newer run exists since this override."}
      </p>
    </div>
  );
}

function OverrideForm({ id, runId, onSaved }: { id: number; runId?: number; onSaved: () => void }) {
  const [verdict, setVerdict] = useState<Verdict>("review");
  const [reason, setReason] = useState("");
  const [analyst, setAnalyst] = useState(() => {
    try {
      return localStorage.getItem("analyst") ?? "";
    } catch {
      return "";
    }
  });
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      localStorage.setItem("analyst", analyst);
    } catch {
      /* storage unavailable; not important */
    }
    try {
      await api.override(id, { verdict, reason, analyst, run_id: runId });
      onSaved();
      setReason("");
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <form onSubmit={submit} className="override-form">
      <h4>Override verdict</h4>
      <div className="row">
        <select value={verdict} onChange={(e) => setVerdict(e.target.value as Verdict)}>
          <option value="pursue">Pursue</option>
          <option value="review">Review</option>
          <option value="reject">Reject</option>
        </select>
        <input required placeholder="Your name" value={analyst} onChange={(e) => setAnalyst(e.target.value)} />
      </div>
      <textarea required placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
      <button type="submit">Save override</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
