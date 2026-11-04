"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useRole, usePaths } from "@/components/shell";
import { Empty, ErrorState, fmt, Loading, Notice, Table, Td } from "@/components/ui";

type Event = { id: number; event_type: string; actor_type: string; actor_user_id: number | null; target_type: string | null; target_id: string | null; payload: Record<string, unknown>; created_at: string };

export default function AuditPage() {
  const { ws } = usePaths();
  const { isManager } = useRole();
  const q = useQuery({ queryKey: ["audit", ws], queryFn: () => api<Event[]>(`${ws}/audit`), enabled: isManager });
  return (
    <>
      <h1 className="text-2xl font-bold">Audit history</h1>
      {!isManager && <Notice kind="warn">Audit history is visible to managers and admins.</Notice>}
      {q.isLoading && <Loading />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.length === 0 && <Empty title="No audit events" />}
      {q.data && q.data.length > 0 && (
        <Table head={["Time", "Event", "Actor", "Target", "Details"]}>
          {q.data.map((e) => (
            <tr key={e.id}>
              <Td>{fmt(e.created_at)}</Td>
              <Td>{e.event_type}</Td>
              <Td>{e.actor_type}{e.actor_user_id ? ` #${e.actor_user_id}` : ""}</Td>
              <Td>{e.target_type ? `${e.target_type} ${e.target_id ?? ""}` : "—"}</Td>
              <Td className="max-w-md break-words text-xs">{JSON.stringify(e.payload)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
