"use client";
import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, type Issue } from "@/lib/api";
import { usePaths } from "@/components/shell";
import { SprintPicker } from "@/components/sprint-picker";
import { useSelection } from "@/lib/context";
import { Badge, Empty, ErrorState, Evidence, fmt, Field, inputCls, Loading, Table, Td } from "@/components/ui";

type IssueRow = Issue & { assignee_id: number | null };
type List = { total: number; items: IssueRow[] };
type Detail = IssueRow & { evidence: { id: number; title: string; excerpt: string; timestamp: string }[]; findings: { id: number; explanation: string; confidence: string }[] };

export default function IssuesPage() {
  const { proj } = usePaths();
  const { sprintId } = useSelection();
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  const all = useQuery({ queryKey: ["issues-all", proj, sprintId], queryFn: () => api<List>(`${proj}/issues${sprintId ? `?sprint_id=${sprintId}` : ""}`) });
  const params = new URLSearchParams();
  if (sprintId) params.set("sprint_id", String(sprintId));
  if (status) params.set("status", status);
  if (assignee === "none") params.set("unassigned", "true");
  else if (assignee) params.set("assignee_id", assignee);
  if (search) params.set("q", search);
  const q = useQuery({ queryKey: ["issues", proj, params.toString()], queryFn: () => api<List>(`${proj}/issues?${params}`) });
  const detail = useQuery({ queryKey: ["issue", proj, open], queryFn: () => api<Detail>(`${proj}/issues/${open}`), enabled: open !== null });

  const statuses = [...new Set(all.data?.items.map((i) => i.status))];
  const people = [...new Map(all.data?.items.filter((i) => i.assignee_id).map((i) => [i.assignee_id, i.assignee])).entries()];

  return (
    <>
      <h1 className="text-2xl font-bold">Issues</h1>
      <div className="flex flex-wrap items-end gap-3">
        <SprintPicker />
        <Field label="Status">
          <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {statuses.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label="Assignee">
          <select className={inputCls} value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">Everyone</option>
            <option value="none">Unassigned</option>
            {people.map(([id, name]) => (
              <option key={String(id)} value={String(id)}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Search">
          <input className={inputCls} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="key or title" />
        </Field>
      </div>
      {q.isLoading && <Loading label="Loading issues" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && <Empty title="No issues match these filters" hint="Clear a filter, or synchronize your integrations." />}
      {q.data && q.data.items.length > 0 && (
        <>
          <p className="text-sm text-muted" aria-live="polite">
            {q.data.total} issue(s)
          </p>
          <Table head={["Key", "Title", "Status", "Assignee", "Due", "Last activity", ""]}>
            {q.data.items.map((i) => (
              <Fragment key={i.id}>
                <tr>
                  <Td>
                    <a href={i.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                      {i.key}
                    </a>
                  </Td>
                  <Td>
                    {i.title} {i.is_blocked && <Badge kind="high">blocked</Badge>}
                  </Td>
                  <Td>{i.status}</Td>
                  <Td>{i.assignee ?? <span className="text-muted">unassigned</span>}</Td>
                  <Td>{i.due_date ?? "—"}</Td>
                  <Td>{fmt(i.last_activity_at)}</Td>
                  <Td>
                    <button className="text-accent underline" aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
                      {open === i.id ? "Hide" : "Evidence"}
                    </button>
                  </Td>
                </tr>
                {open === i.id && (
                  <tr>
                    <td colSpan={7} className="border-b border-border bg-bg p-3 text-sm">
                      {detail.isLoading && "Loading…"}
                      {detail.isError && <ErrorState error={detail.error} />}
                      {detail.data && (
                        <div className="space-y-2">
                          {detail.data.description && <p className="whitespace-pre-wrap">{detail.data.description}</p>}
                          <p className="font-medium">Related evidence</p>
                          {detail.data.evidence.length === 0 && <p className="text-muted">No evidence records.</p>}
                          <ul className="space-y-1">
                            {detail.data.evidence.map((e) => (
                              <li key={e.id}>
                                <Evidence ids={[e.id]} /> {e.title}
                              </li>
                            ))}
                          </ul>
                          {detail.data.findings.map((f) => (
                            <p key={f.id}>
                              <Badge kind={f.confidence === "confirmed" ? "high" : "medium"}>{f.confidence}</Badge> {f.explanation}
                            </p>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </Table>
        </>
      )}
    </>
  );
}
