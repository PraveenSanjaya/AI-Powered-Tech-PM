"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Issue, type PR } from "@/lib/api";
import { usePaths } from "@/components/shell";
import { Badge, Card, Empty, ErrorState, fmt, Loading, Notice, Table, Td } from "@/components/ui";
import { SprintPicker } from "@/components/sprint-picker";
import { useSelection } from "@/lib/context";

type Dash = {
  project: { name: string; key: string };
  sprint: { id: number; name: string; goal: string; start_date: string; end_date: string } | null;
  counts: { total: number; by_status: Record<string, number>; by_category: Record<string, number> };
  completed: number;
  remaining: number;
  blocked: Issue[];
  potentially_blocked: { finding_id: number; explanation: string; confidence: string }[];
  overdue: Issue[];
  prs_awaiting_review: PR[];
  failed_checks: PR[];
  assigned_work: { name: string; open_issues: string[] }[];
  workload_note: string;
  recent_changes: { id: number; title: string; kind: string; provider: string; timestamp: string; url: string; is_demo: boolean }[];
  freshness: { label: string; stale: boolean; last_synced_at: string | null; is_demo: boolean };
  integrations: { provider: string; status: string; is_demo: boolean; last_success_at: string | null; last_error: string | null }[];
  burndown: { available: boolean; reason: string };
};

const CAT_COLOR: Record<string, string> = { Done: "#16a34a", "In Progress": "#2563eb", "In Review": "#7c3aed", "To Do": "#64748b" };

export default function Dashboard() {
  const { proj } = usePaths();
  const { sprintId } = useSelection();
  const q = useQuery({
    queryKey: ["dashboard", proj, sprintId],
    queryFn: () => api<Dash>(`${proj}/dashboard${sprintId ? `?sprint_id=${sprintId}` : ""}`),
  });
  if (q.isLoading) return <Loading label="Loading dashboard" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  const statusData = Object.entries(d.counts.by_status).map(([name, value]) => ({ name, value }));
  const disconnected = d.integrations.filter((i) => i.status !== "connected");

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            {d.project.name} <span className="text-muted">({d.project.key})</span>
          </h1>
          {d.sprint ? (
            <p className="text-sm text-muted">
              {d.sprint.name}: {d.sprint.start_date} → {d.sprint.end_date}
            </p>
          ) : (
            <p className="text-sm text-muted">No sprint selected.</p>
          )}
        </div>
        <SprintPicker />
      </div>

      {d.integrations.length === 0 && (
        <Notice kind="warn">
          No integrations are connected, so there is no data to show. <Link className="underline" href="/integrations">Connect an integration</Link>.
        </Notice>
      )}
      {disconnected.length > 0 && (
        <Notice kind="warn">
          Disconnected or failing integration(s): {disconnected.map((i) => `${i.provider} (${i.status})`).join(", ")}. Data may be out of date.{" "}
          <Link className="underline" href="/integrations">Review</Link>
        </Notice>
      )}
      <Notice kind={d.freshness.stale ? "warn" : "info"}>
        <strong>Synchronization freshness:</strong> {d.freshness.label} {d.freshness.stale && "Treat the numbers below as possibly stale."}
      </Notice>

      {d.sprint && (
        <Card title="Sprint goal">
          <p>{d.sprint.goal || "No goal recorded."}</p>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Issues by status">
          {d.counts.total === 0 ? (
            <Empty title="No issues in this sprint" hint="Run a synchronization on the Integrations page." />
          ) : (
            <>
              <div className="h-48" role="img" aria-label={`Issue counts by status: ${statusData.map((s) => `${s.name} ${s.value}`).join(", ")}`}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={statusData} margin={{ left: -20 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="name" tick={{ fill: "var(--text)", fontSize: 12 }} />
                    <YAxis allowDecimals={false} tick={{ fill: "var(--text)", fontSize: 12 }} />
                    <Tooltip />
                    <Bar dataKey="value">
                      {statusData.map((s) => (
                        <Cell key={s.name} fill={CAT_COLOR[s.name] ?? "#475569"} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <ul className="mt-2 flex flex-wrap gap-3 text-sm">
                {statusData.map((s) => (
                  <li key={s.name}>
                    {s.name}: <strong>{s.value}</strong>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
        <Card title="Completed vs remaining">
          <p className="text-3xl font-bold">
            {d.completed} <span className="text-base font-normal text-muted">of {d.counts.total} issues done</span>
          </p>
          <div className="mt-2 h-3 w-full overflow-hidden rounded bg-bg" role="progressbar" aria-valuemin={0} aria-valuemax={d.counts.total} aria-valuenow={d.completed} aria-label="Completed issues">
            <div className="h-full bg-green-600" style={{ width: `${d.counts.total ? (100 * d.completed) / d.counts.total : 0}%` }} />
          </div>
          <p className="mt-2 text-sm text-muted">{d.remaining} remaining.</p>
          <p className="mt-3 text-sm">
            <strong>Burndown:</strong> {d.burndown.available ? "available" : d.burndown.reason}
          </p>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title={`Blocked or potentially blocked (${d.blocked.length + d.potentially_blocked.length})`}>
          {d.blocked.length + d.potentially_blocked.length === 0 && <Empty title="Nothing flagged" />}
          <ul className="space-y-2 text-sm">
            {d.blocked.map((i) => (
              <li key={i.id}>
                <Badge kind="high">confirmed blocked</Badge> <strong>{i.key}</strong> {i.title}
                {i.blocked_reason && <span className="block text-muted">{i.blocked_reason}</span>}
              </li>
            ))}
            {d.potentially_blocked.map((p) => (
              <li key={p.finding_id}>
                <Badge kind="medium">possible</Badge> {p.explanation}
              </li>
            ))}
          </ul>
          <Link className="mt-2 inline-block text-sm underline" href="/risks">
            See evidence on Risks &amp; blockers
          </Link>
        </Card>
        <Card title={`Overdue work (${d.overdue.length})`}>
          {d.overdue.length === 0 ? <Empty title="No overdue issues" /> : (
            <ul className="space-y-1 text-sm">
              {d.overdue.map((i) => (
                <li key={i.id}>
                  <strong>{i.key}</strong> {i.title} — due {i.due_date} ({i.assignee ?? "unassigned"})
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={`Pull requests awaiting review (${d.prs_awaiting_review.length})`}>
          {d.prs_awaiting_review.length === 0 ? <Empty title="No PRs awaiting review" /> : (
            <ul className="space-y-1 text-sm">
              {d.prs_awaiting_review.map((p) => (
                <li key={p.id}>
                  #{p.number} {p.title} <span className="text-muted">({p.repo}, {p.author ?? "unknown"})</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={`Failed checks (${d.failed_checks.length})`}>
          {d.failed_checks.length === 0 ? <Empty title="No failing checks" /> : (
            <ul className="space-y-1 text-sm">
              {d.failed_checks.map((p) => (
                <li key={p.id}>
                  #{p.number} {p.title} <span className="text-muted">({p.repo})</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Assigned open work by team member">
        {d.assigned_work.length === 0 ? <Empty title="No open assigned work" /> : (
          <Table head={["Person", "Open issues"]}>
            {d.assigned_work.map((a) => (
              <tr key={a.name}>
                <Td>{a.name}</Td>
                <Td>{a.open_issues.join(", ")}</Td>
              </tr>
            ))}
          </Table>
        )}
        <p className="mt-2 text-xs text-muted">{d.workload_note}</p>
      </Card>

      <Card title="Recent project changes">
        {d.recent_changes.length === 0 ? <Empty title="No recent changes recorded" /> : (
          <ul className="space-y-1 text-sm">
            {d.recent_changes.map((c) => (
              <li key={c.id}>
                {fmt(c.timestamp)} — {c.title} <span className="text-muted">({c.provider})</span> {c.is_demo && <Badge kind="demo">demo</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
