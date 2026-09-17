import type { RunStatus, Verdict } from "./api";

const STATUS_TEXT: Record<RunStatus, string> = {
  pending: "Pending",
  running: "Enriching…",
  complete: "Complete",
  partial: "Partial – data missing",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: RunStatus | null }) {
  if (!status) return <span className="badge">—</span>;
  return <span className={`badge status-${status}`}>{STATUS_TEXT[status]}</span>;
}

export function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <span className="muted">—</span>;
  return <span className={`badge verdict-${verdict}`}>{verdict}</span>;
}

/** A score that can never be mistaken for a complete one when it isn't. */
export function ScoreText({ score, status }: { score: number | null; status: RunStatus | null }) {
  if (status === "pending" || status === "running") return <span className="muted">…</span>;
  if (score === null) return <span className="muted">no score</span>;
  return (
    <span>
      {score}
      {status === "partial" && <span className="partial-mark" title="Based on partial data"> (partial)</span>}
    </span>
  );
}
