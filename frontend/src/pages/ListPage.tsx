import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, isInProgress, type Page } from "../api";
import { ScoreText, StatusBadge, VerdictBadge } from "../components";

const PAGE_SIZE = 25;
const POLL_MS = 3000;

export default function ListPage() {
  // Filters/sort/page live in the URL so a filtered view can be linked or reloaded.
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sort = params.get("sort") ?? "created_at";
  const order = params.get("order") ?? "desc";
  const offset = Number(params.get("offset") ?? 0);

  const load = useCallback(async () => {
    const query = new URLSearchParams(params);
    query.set("limit", String(PAGE_SIZE));
    try {
      setPage(await api.list(query));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [params]);

  useEffect(() => {
    load();
  }, [load]);

  // Keep polling only while something on screen is still being enriched.
  const anyInProgress = page?.items.some((i) => isInProgress(i.status)) ?? false;
  useEffect(() => {
    if (!anyInProgress) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [anyInProgress, load]);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    if (!("offset" in changes)) next.delete("offset"); // any filter change resets paging
    setParams(next);
  };

  const sortBy = (column: string) =>
    update({ sort: column, order: sort === column && order === "desc" ? "asc" : "desc" });
  const arrow = (column: string) => (sort === column ? (order === "desc" ? " ▼" : " ▲") : "");

  return (
    <>
      <SubmitForm onCreated={load} />

      <h2>Assessments</h2>
      <div className="filters">
        <input
          placeholder="Search label or address"
          defaultValue={params.get("q") ?? ""}
          onKeyDown={(e) => e.key === "Enter" && update({ q: e.currentTarget.value })}
          onBlur={(e) => e.currentTarget.value !== (params.get("q") ?? "") && update({ q: e.currentTarget.value })}
        />
        <select value={params.get("status") ?? ""} onChange={(e) => update({ status: e.target.value })}>
          <option value="">Any status</option>
          <option value="pending">Pending</option>
          <option value="running">Enriching</option>
          <option value="complete">Complete</option>
          <option value="partial">Partial</option>
          <option value="failed">Failed</option>
        </select>
        <select value={params.get("verdict") ?? ""} onChange={(e) => update({ verdict: e.target.value })}>
          <option value="">Any verdict</option>
          <option value="pursue">Pursue</option>
          <option value="review">Review</option>
          <option value="reject">Reject</option>
        </select>
      </div>

      {error && <p className="error">Could not load assessments: {error}</p>}
      {page && (
        <>
          <table>
            <thead>
              <tr>
                <th className="sortable" onClick={() => sortBy("label")}>Label{arrow("label")}</th>
                <th>Location</th>
                <th className="sortable" onClick={() => sortBy("status")}>Status{arrow("status")}</th>
                <th className="sortable" onClick={() => sortBy("score")}>Score{arrow("score")}</th>
                <th>Verdict</th>
                <th>Runs</th>
                <th className="sortable" onClick={() => sortBy("created_at")}>Submitted{arrow("created_at")}</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.id}>
                  <td><Link to={`/assessments/${item.id}`}>{item.label}</Link></td>
                  <td className="muted">{item.location}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td><ScoreText score={item.score} status={item.status} /></td>
                  <td>
                    <VerdictBadge verdict={item.effective_verdict} />
                    {item.override_verdict && (
                      <span className="muted small"> analyst (machine: {item.machine_verdict ?? "—"})</span>
                    )}
                  </td>
                  <td>{item.run_count}</td>
                  <td className="muted">{fmtDate(item.created_at)}</td>
                </tr>
              ))}
              {page.items.length === 0 && (
                <tr><td colSpan={7} className="muted">No assessments match.</td></tr>
              )}
            </tbody>
          </table>
          <div className="pager">
            <button disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE_SIZE)) })}>
              ← Prev
            </button>
            <span>
              {page.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
            </span>
            <button
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => update({ offset: String(offset + PAGE_SIZE) })}
            >
              Next →
            </button>
          </div>
        </>
      )}
    </>
  );
}

function SubmitForm({ onCreated }: { onCreated: () => void }) {
  const [mode, setMode] = useState<"address" | "coords">("address");
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string; id?: number } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const body =
        mode === "address"
          ? { label, address }
          : { label, lat: Number(lat), lon: Number(lon) };
      if (mode === "coords" && (lat.trim() === "" || lon.trim() === "" || isNaN(body.lat!) || isNaN(body.lon!))) {
        throw new Error("latitude and longitude must both be numbers");
      }
      const created = await api.create(body);
      setMessage({ kind: "ok", text: `Queued "${created.label}". It will appear below and update on its own.`, id: created.id });
      setLabel("");
      setAddress("");
      setLat("");
      setLon("");
      onCreated();
    } catch (err) {
      setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>Submit a location</h2>
      <div className="row">
        <label>
          Label
          <input required maxLength={200} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Denver warehouse option B" />
        </label>
        <label>
          <input type="radio" checked={mode === "address"} onChange={() => setMode("address")} /> US address
        </label>
        <label>
          <input type="radio" checked={mode === "coords"} onChange={() => setMode("coords")} /> Latitude / longitude
        </label>
      </div>
      <div className="row">
        {mode === "address" ? (
          <label className="grow">
            Address
            <input required value={address} onChange={(e) => setAddress(e.target.value)} placeholder="1437 Bannock St, Denver, CO 80202" />
            <span className="muted small">Full street address with house number. City names alone won't geocode; use lat/lon for those.</span>
          </label>
        ) : (
          <>
            <label>
              Latitude
              <input required value={lat} onChange={(e) => setLat(e.target.value)} placeholder="39.7392" />
            </label>
            <label>
              Longitude
              <input required value={lon} onChange={(e) => setLon(e.target.value)} placeholder="-104.9903" />
            </label>
          </>
        )}
        <button type="submit" disabled={busy}>Submit</button>
      </div>
      {message && (
        <p className={message.kind === "error" ? "error" : "ok"}>
          {message.text} {message.id && <Link to={`/assessments/${message.id}`}>Open</Link>}
        </p>
      )}
    </form>
  );
}
