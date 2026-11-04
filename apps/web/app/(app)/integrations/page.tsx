"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useRole, usePaths } from "@/components/shell";
import { Badge, Button, Card, Empty, ErrorState, fmt, inputCls, Loading, Notice, Table, Td } from "@/components/ui";

type Integration = {
  provider: string;
  label: string;
  live_connector: boolean;
  live_note: string;
  connection: null | { id: number; status: string; is_demo: boolean; capabilities: string[]; last_success_at: string | null; last_error: string | null };
  recent_syncs: { id: number; mode: string; status: string; started_at: string; error: string | null; stats: Record<string, number> }[];
};
type Identity = { id: number; provider: string; display_name: string; email: string | null; status: string; user_id: number | null };
type Member = { user_id: number; name: string; email: string; role: string };

export default function IntegrationsPage() {
  const { ws } = usePaths();
  const { isManager, isAdmin } = useRole();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["integrations", ws], queryFn: () => api<Integration[]>(`${ws}/integrations`) });
  const ids = useQuery({ queryKey: ["identities", ws], queryFn: () => api<Identity[]>(`${ws}/identities`) });
  const members = useQuery({ queryKey: ["members", ws], queryFn: () => api<Member[]>(`${ws}/members`) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["integrations"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["identities"] });
  };
  const sync = useMutation({ mutationFn: (id: number) => api(`${ws}/integrations/${id}/sync`, { method: "POST" }), onSettled: refresh });
  const disconnect = useMutation({ mutationFn: (v: { id: number; purge: boolean }) => api(`${ws}/integrations/${v.id}?purge_data=${v.purge}`, { method: "DELETE" }), onSettled: refresh });
  const demo = useMutation({ mutationFn: () => api(`${ws}/integrations/demo`, { method: "POST" }), onSettled: refresh });
  const map = useMutation({ mutationFn: (v: { id: number; user_id: number | null }) => api(`${ws}/identities/${v.id}`, { method: "PUT", json: { user_id: v.user_id } }), onSettled: refresh });

  const err = sync.error ?? disconnect.error ?? demo.error ?? map.error;
  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Integrations</h1>
        {isAdmin && <Button variant="secondary" onClick={() => demo.mutate()} disabled={demo.isPending}>Connect fictional demo integrations</Button>}
      </div>
      <Notice kind="warn">
        Only <strong>demo connectors</strong> are implemented in this build. Live GitHub, Jira, Slack, Google Calendar and Notion connectors are not available yet, so no real service is contacted.
      </Notice>
      {err && <ErrorState error={err} />}
      {q.isLoading && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      <div className="grid gap-4 md:grid-cols-2">
        {q.data?.map((i) => {
          const c = i.connection;
          return (
            <Card key={i.provider} title={i.label}>
              <div className="space-y-2 text-sm" data-testid={`integration-${i.provider}`}>
                <p>
                  {c ? (
                    <>
                      <Badge kind={c.status === "connected" ? "ok" : "high"}>{c.status}</Badge> {c.is_demo && <Badge kind="demo">demo (simulated)</Badge>}
                    </>
                  ) : (
                    <Badge>not connected</Badge>
                  )}
                </p>
                <p className="text-muted">{i.live_note}</p>
                {c && <p>Last successful sync: {fmt(c.last_success_at)}</p>}
                {c?.status === "disconnected" && <p className="text-warn">Disconnected: imported data is no longer being refreshed and may be stale.</p>}
                {c?.last_error && <p role="alert" className="text-danger">Error: {c.last_error}</p>}
                {c && c.capabilities.length > 0 && <p className="text-muted">Can perform: {c.capabilities.join(", ")}</p>}
                {i.recent_syncs.length > 0 && (
                  <ul className="text-xs text-muted">
                    {i.recent_syncs.slice(0, 3).map((s) => (
                      <li key={s.id}>
                        {fmt(s.started_at)} · {s.mode} · {s.status} {s.error && `— ${s.error}`}
                      </li>
                    ))}
                  </ul>
                )}
                {c && isManager && (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" disabled={sync.isPending || c.status === "disconnected"} onClick={() => sync.mutate(c.id)}>Sync now</Button>
                    {isAdmin && c.status !== "disconnected" && (
                      <>
                        <Button variant="secondary" onClick={() => disconnect.mutate({ id: c.id, purge: false })}>Disconnect</Button>
                        <Button variant="danger" onClick={() => confirm(`Disconnect ${i.label} and delete data imported from it?`) && disconnect.mutate({ id: c.id, purge: true })}>Disconnect &amp; delete data</Button>
                      </>
                    )}
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
      <Card title="External identities">
        <p className="mb-2 text-sm text-muted">People are never linked across tools by display name. Confirm each mapping explicitly; unresolved identities stay unresolved.</p>
        {ids.isLoading && <Loading />}
        {ids.data?.length === 0 && <Empty title="No identities imported yet" />}
        {ids.data && ids.data.length > 0 && (
          <Table head={["Provider", "Name", "Email", "Status", "Linked workspace user"]}>
            {ids.data.map((i) => (
              <tr key={i.id}>
                <Td>{i.provider}</Td>
                <Td>{i.display_name}</Td>
                <Td>{i.email ?? "—"}</Td>
                <Td><Badge kind={i.status === "confirmed" ? "ok" : "medium"}>{i.status}</Badge></Td>
                <Td>
                  {isManager ? (
                    <select aria-label={`Link ${i.display_name} (${i.provider})`} className={inputCls} value={i.user_id ?? ""} onChange={(e) => map.mutate({ id: i.id, user_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">Unresolved</option>
                      {members.data?.map((m) => <option key={m.user_id} value={m.user_id}>{m.name} ({m.email})</option>)}
                    </select>
                  ) : (
                    i.user_id ?? "—"
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
