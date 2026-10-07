"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type Citation, type Issue } from "@/lib/api";
import { usePaths } from "@/components/shell";
import { SprintPicker, useSprints } from "@/components/sprint-picker";
import { useSelection } from "@/lib/context";
import Markdown from "@/components/markdown";
import { Badge, Button, Card, Empty, ErrorState, Loading, Notice } from "@/components/ui";

type Summary = { id: number; markdown: string; label: string; citations: Citation[]; freshness: { stale: boolean; label: string } };

export default function SprintPage() {
  const { proj, ws, projectId } = usePaths();
  const { sprintId } = useSelection();
  const sprints = useSprints();
  const sprint = sprints.data?.find((s) => (sprintId ? s.id === sprintId : s.state === "active"));
  const issues = useQuery({
    queryKey: ["issues-sprint", proj, sprint?.id],
    queryFn: () => api<{ items: Issue[] }>(`${proj}/issues?sprint_id=${sprint!.id}`),
    enabled: !!sprint,
  });
  const gen = useMutation({ mutationFn: () => api<Summary>(`${ws}/summaries`, { method: "POST", json: { project_id: projectId, sprint_id: sprint?.id ?? null } }) });

  if (sprints.isLoading) return <Loading />;
  if (sprints.isError) return <ErrorState error={sprints.error} onRetry={() => sprints.refetch()} />;
  const groups = ["todo", "in_progress", "done"] as const;
  const titles = { todo: "To do", in_progress: "In progress", done: "Done" };
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-bold">Sprint detail</h1>
        <SprintPicker />
      </div>
      {!sprint ? (
        <Empty title="No sprint found" hint="Synchronize Jira or create a sprint." />
      ) : (
        <>
          <Card title={sprint.name}>
            <p className="text-sm text-muted">
              {sprint.start_date} → {sprint.end_date} · {sprint.state}
            </p>
            <p className="mt-2">
              <strong>Goal:</strong> {sprint.goal || "not set"}
            </p>
          </Card>
          <div className="grid gap-4 md:grid-cols-3">
            {groups.map((g) => {
              const items = issues.data?.items.filter((i) => i.status_category === g) ?? [];
              return (
                <Card key={g} title={`${titles[g]} (${items.length})`}>
                  {issues.isLoading && <Loading />}
                  {!issues.isLoading && items.length === 0 && <Empty title="None" />}
                  <ul className="space-y-2 text-sm">
                    {items.map((i) => (
                      <li key={i.id}>
                        <strong>{i.key}</strong> {i.title} {i.is_blocked && <Badge kind="high">blocked</Badge>}
                        <span className="block text-muted">
                          {i.status} · {i.assignee ?? "unassigned"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              );
            })}
          </div>
          <Card title="Sprint summary" actions={<Button onClick={() => gen.mutate()} disabled={gen.isPending}>{gen.isPending ? "Generating…" : "Generate summary"}</Button>}>
            {!gen.data && !gen.isError && <p className="text-sm text-muted">Generates an evidence-backed summary from synchronized data.</p>}
            {gen.isError && <ErrorState error={gen.error} onRetry={() => gen.mutate()} />}
            {gen.data && (
              <>
                <Notice kind="demo">{gen.data.label}</Notice>
                <Markdown text={gen.data.markdown} citations={gen.data.citations} />
              </>
            )}
          </Card>
        </>
      )}
    </>
  );
}
