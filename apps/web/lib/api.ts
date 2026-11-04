// All requests go to the same origin (/api, proxied to the backend). No provider secrets ever reach the browser.
let csrfToken = "";
export const setCsrf = (t: string) => {
  csrfToken = t;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.json !== undefined) headers["content-type"] = "application/json";
  const method = (init.method || "GET").toUpperCase();
  if (method !== "GET" && csrfToken) headers["x-csrf-token"] = csrfToken;
  const res = await fetch(`/api${path}`, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON error body */
  }
  if (!res.ok) {
    const d = data as { detail?: unknown } | null;
    const detail = typeof d?.detail === "string" ? d.detail : Array.isArray(d?.detail) ? "Invalid input" : res.statusText;
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return data as T;
}

export type Me = {
  user: { id: number; email: string; name: string };
  csrf_token: string;
  demo_mode: boolean;
  workspaces: { id: number; name: string; role: "admin" | "manager" | "member" }[];
};
export type Citation = {
  id: number;
  title: string;
  url: string;
  provider: string;
  kind: string;
  timestamp: string;
  excerpt: string;
  is_demo: boolean;
};
export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  evidence_ids: number[];
  meta: {
    label?: string;
    generator?: string;
    proposal_id?: number | null;
    citations?: Citation[];
    freshness?: { label: string; stale: boolean };
    fallback?: boolean;
    llm_error?: string;
    workflow_run_id?: number;
  };
};
export type Issue = {
  id: number;
  key: string;
  title: string;
  status: string;
  status_category: string;
  priority: string | null;
  assignee: string | null;
  is_blocked: boolean;
  blocked_reason: string | null;
  due_date: string | null;
  url: string;
  last_activity_at: string;
  description: string;
};
export type PR = {
  id: number;
  number: number;
  repo: string;
  title: string;
  author: string | null;
  state: string;
  draft: boolean;
  review_state: string;
  checks_state: string;
  issue_key: string | null;
  url: string;
  checks: { name: string; conclusion: string | null; status: string; required: boolean }[];
};
export type Finding = {
  id: number;
  type: string;
  severity: "low" | "medium" | "high";
  confidence: "confirmed" | "likely" | "possible";
  method: string;
  explanation: string;
  status: string;
  evidence_ids: number[];
  detected_at: string;
  issue_key: string | null;
  pr_number: number | null;
};
export type Action = {
  id: number;
  provider: string;
  action_type: string;
  target: Record<string, string>;
  status: string;
  reason: string;
  version: number;
  payload: Record<string, unknown>;
  requested_by: { id: number; name: string } | null;
  proposed_by: string;
  expires_at: string;
  is_demo: boolean;
  simulated_notice: string | null;
  external_id: string | null;
  external_url: string | null;
  recipients: string[];
  citations?: Citation[];
  executions?: { attempt: number; status: string; started_at: string; result: Record<string, unknown>; error: string | null }[];
  versions?: { version: number }[];
};
