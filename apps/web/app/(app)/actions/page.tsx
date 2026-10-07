"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api, type Action } from "@/lib/api";
import { useRole, usePaths } from "@/components/shell";
import { Badge, Button, Card, Empty, ErrorState, Evidence, fmt, inputCls, Loading, Notice, Table, Td } from "@/components/ui";

const STATUS_KIND: Record<string, string> = {
  pending_approval: "medium", approved: "info", succeeded: "ok", failed: "high", unknown_outcome: "high", rejected: "low", expired: "low", executing: "info",
};

function Detail({ id }: { id: number }) {
  const { ws } = usePaths();
  const { isManager } = useRole();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["action", ws, id], queryFn: () => api<Action>(`${ws}/actions/${id}`) });
  const [edit, setEdit] = useState<string | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["action"] });
    qc.invalidateQueries({ queryKey: ["actions"] });
  };
  const run = useMutation({
    mutationFn: (v: { verb: string; body?: unknown; method?: string; path?: string }) =>
      api<Action>(`${ws}/actions/${id}${v.path ?? "/" + v.verb}`, { method: v.method ?? "POST", json: v.body ?? {} }),
    onSuccess: () => {
      setEdit(null);
      refresh();
    },
  });
  useEffect(() => setEdit(null), [id]);
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data!;
  const text = String(a.payload.body ?? a.payload.text ?? JSON.stringify(a.payload, null, 2));
  const field = a.action_type === "slack_message" ? "text" : a.action_type === "jira_comment" ? "body" : null;
  const editable = isManager && ["pending_approval", "approved"].includes(a.status) && field;
  return (
    <Card title={`Action #${a.id}: ${a.action_type.replace("_", " ")}`}>
      <div className="space-y-3 text-sm">
        {a.simulated_notice && <Notice kind="demo">{a.simulated_notice}</Notice>}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-muted">Status</dt>
          <dd><Badge kind={STATUS_KIND[a.status] ?? "low"}>{a.status.replace("_", " ")}</Badge> (payload version {a.version})</dd>
          <dt className="text-muted">Provider / target</dt>
          <dd>{a.provider} · {JSON.stringify(a.target)}</dd>
          <dt className="text-muted">Recipients</dt>
          <dd>{a.recipients.join(", ")}</dd>
          <dt className="text-muted">Requested by</dt>
          <dd>{a.requested_by?.name ?? "system"} (drafted by {a.proposed_by})</dd>
          <dt className="text-muted">Reason</dt>
          <dd>{a.reason || "—"}</dd>
          <dt className="text-muted">Expires</dt>
          <dd>{fmt(a.expires_at)}</dd>
        </dl>
        <div>
          <p className="mb-1 font-medium">Exact content that will be sent</p>
          {edit === null ? (
            <pre data-testid="payload" className="whitespace-pre-wrap rounded border border-border bg-bg p-3 text-sm">{text}</pre>
          ) : (
            <textarea aria-label="Edit content" className={`${inputCls} h-48 w-full font-mono`} value={edit} onChange={(e) => setEdit(e.target.value)} />
          )}
        </div>
        {a.citations && a.citations.length > 0 && (
          <div>
            <p className="font-medium">Supporting evidence</p>
            <Evidence ids={a.citations.map((c) => c.id)} />
          </div>
        )}
        {run.isError && <ErrorState error={run.error} />}
        {isManager ? (
          <div className="flex flex-wrap gap-2">
            {editable && edit === null && <Button variant="secondary" onClick={() => setEdit(text)}>Edit</Button>}
            {edit !== null && field && (
              <>
                <Button onClick={() => run.mutate({ verb: "payload", method: "PUT", path: "/payload", body: { payload: { [field]: edit } } })} disabled={run.isPending}>Save as new version</Button>
                <Button variant="secondary" onClick={() => setEdit(null)}>Cancel edit</Button>
              </>
            )}
            {a.status === "pending_approval" && edit === null && (
              <>
                <Button onClick={() => run.mutate({ verb: "approve", body: { version: a.version } })} disabled={run.isPending}>Approve version {a.version}</Button>
                <Button variant="danger" onClick={() => run.mutate({ verb: "reject", body: { note: "" } })} disabled={run.isPending}>Reject</Button>
              </>
            )}
            {["approved", "failed", "unknown_outcome"].includes(a.status) && edit === null && (
              <Button onClick={() => run.mutate({ verb: "execute" })} disabled={run.isPending}>
                {a.status === "approved" ? (a.is_demo ? "Execute (simulated)" : "Execute") : "Retry execution"}
              </Button>
            )}
            {a.status === "succeeded" && (
              <Button variant="secondary" onClick={() => run.mutate({ verb: "execute" })} disabled={run.isPending}>Retry (safe: no duplicate)</Button>
            )}
          </div>
        ) : (
          <p className="text-muted">Only managers and admins can approve or execute actions.</p>
        )}
        {a.status === "unknown_outcome" && <Notice kind="warn">The provider response was lost. Retrying first checks whether the action already happened; it will not post twice.</Notice>}
        {a.status === "succeeded" && (
          <Notice>
            Result: external id <code>{a.external_id}</code>{" "}
            {a.external_url && <span>· <a className="underline" href={a.external_url} target="_blank" rel="noopener noreferrer">{a.external_url}</a></span>}
            {a.is_demo && " (simulated, not a real service)"}
          </Notice>
        )}
        {a.executions && a.executions.length > 0 && (
          <div>
            <p className="font-medium">Execution attempts</p>
            <ul className="text-muted">
              {a.executions.map((e) => (
                <li key={e.attempt}>
                  #{e.attempt} {e.status} at {fmt(e.started_at)} {e.error && `— ${e.error}`} {e.result?.reconciled ? "(reconciled with provider)" : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}

function Inner() {
  const { ws } = usePaths();
  const params = useSearchParams();
  const [sel, setSel] = useState<number | null>(params.get("id") ? Number(params.get("id")) : null);
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["actions", ws, status], queryFn: () => api<Action[]>(`${ws}/actions${status ? `?status=${status}` : ""}`) });
  return (
    <>
      <h1 className="text-2xl font-bold">Action approvals</h1>
      <label className="flex w-60 flex-col gap-1 text-sm">
        <span className="text-muted">Status</span>
        <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          {["pending_approval", "approved", "succeeded", "failed", "unknown_outcome", "rejected", "expired"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      {q.isLoading && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.length === 0 && <Empty title="No actions" hint="Ask the assistant to draft a Jira comment or Slack update." />}
      {q.data && q.data.length > 0 && (
        <Table head={["#", "Type", "Target", "Status", "Requested by", ""]}>
          {q.data.map((a) => (
            <tr key={a.id}>
              <Td>{a.id}</Td>
              <Td>{a.action_type.replace("_", " ")} {a.is_demo && <Badge kind="demo">demo</Badge>}</Td>
              <Td>{Object.values(a.target).join(", ")}</Td>
              <Td><Badge kind={STATUS_KIND[a.status] ?? "low"}>{a.status.replace("_", " ")}</Badge></Td>
              <Td>{a.requested_by?.name ?? a.proposed_by}</Td>
              <Td><button className="text-accent underline" onClick={() => setSel(a.id)}>Review</button></Td>
            </tr>
          ))}
        </Table>
      )}
      {sel !== null && <Detail id={sel} />}
    </>
  );
}

export default function ActionsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Inner />
    </Suspense>
  );
}
