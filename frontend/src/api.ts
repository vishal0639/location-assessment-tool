// Types mirror backend/app/schemas.py. Kept by hand: two screens don't justify codegen.

export type RunStatus = "pending" | "running" | "complete" | "partial" | "failed";
export type Verdict = "pursue" | "review" | "reject";

export interface ListItem {
  id: number;
  label: string;
  location: string;
  created_at: string;
  run_count: number;
  status: RunStatus | null;
  score: number | null;
  machine_verdict: Verdict | null;
  override_verdict: Verdict | null;
  effective_verdict: Verdict | null;
}

export interface Page {
  items: ListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface RunSummary {
  id: number;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  error: string | null;
  lat: number | null;
  lon: number | null;
  matched_address: string | null;
  scoring_version: string | null;
  score: number | null;
  points_earned: number | null;
  points_available: number | null;
  points_possible: number | null;
  machine_verdict: Verdict | null;
  verdict_reason: string | null;
}

export interface Factor {
  factor: string;
  label: string;
  status: "ok" | "unavailable";
  raw_value: unknown;
  derived_value: string | null;
  points: number | null;
  max_points: number;
  explanation: string;
  source: string;
  source_fetch_id: number | null;
  fetched_at: string | null;
}

export interface Fetch {
  id: number;
  source: string;
  status: "ok" | "error";
  error_kind: string | null;
  error_detail: string | null;
  http_status: number | null;
  request_url: string;
  raw_payload: unknown;
  fetched_at: string;
  duration_ms: number;
}

export interface RunDetail extends RunSummary {
  factors: Factor[];
  fetches: Fetch[];
}

export interface Override {
  id: number;
  run_id: number | null;
  verdict: Verdict;
  reason: string;
  analyst: string;
  created_at: string;
  run_score: number | null;
  run_machine_verdict: Verdict | null;
}

export interface Detail {
  id: number;
  label: string;
  input_address: string | null;
  input_lat: number | null;
  input_lon: number | null;
  created_at: string;
  latest_run_id: number | null;
  runs: RunSummary[];
  run: RunDetail | null;
  current_override: Override | null;
  overrides: Override[];
}

export const SOURCE_NAMES: Record<string, string> = {
  census_geocoder: "US Census Geocoder",
  usgs_epqs: "USGS Elevation Point Query",
  open_meteo: "Open-Meteo Archive",
  fema_nfhl: "FEMA National Flood Hazard Layer",
};

export const isInProgress = (s: RunStatus | null | undefined) => s === "pending" || s === "running";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      const detail = body.detail;
      message = Array.isArray(detail)
        ? detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, "")).join("; ")
        : String(detail ?? message);
    } catch {
      /* non-JSON error body: keep the status line */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  list: (params: URLSearchParams) => request<Page>(`/api/assessments?${params}`),
  get: (id: number, runId?: number) =>
    request<Detail>(`/api/assessments/${id}${runId ? `?run_id=${runId}` : ""}`),
  create: (body: { label: string; address?: string; lat?: number; lon?: number }) =>
    request<Detail>("/api/assessments", { method: "POST", body: JSON.stringify(body) }),
  rerun: (id: number) => request<Detail>(`/api/assessments/${id}/runs`, { method: "POST" }),
  override: (id: number, body: { verdict: Verdict; reason: string; analyst: string; run_id?: number }) =>
    request<Detail>(`/api/assessments/${id}/overrides`, { method: "POST", body: JSON.stringify(body) }),
};

export const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
