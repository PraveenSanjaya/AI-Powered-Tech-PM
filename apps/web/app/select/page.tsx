"use client";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSelection } from "@/lib/context";
import { useMe } from "@/components/shell";
import { Button, Card, Empty, ErrorState, Loading } from "@/components/ui";

type Project = { id: number; key: string; name: string; description: string };

export default function SelectPage() {
  const router = useRouter();
  const me = useMe();
  const sel = useSelection();
  const [ws, setWs] = useState<number | null>(null);
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.error, router]);
  useEffect(() => {
    if (me.data && ws === null && me.data.workspaces.length) setWs(sel.workspaceId ?? me.data.workspaces[0].id);
  }, [me.data, ws, sel.workspaceId]);
  const projects = useQuery({ queryKey: ["projects", ws], queryFn: () => api<Project[]>(`/workspaces/${ws}/projects`), enabled: ws !== null });

  if (me.isLoading) return <Loading />;
  if (me.error && !(me.error instanceof ApiError && me.error.status === 401)) return <ErrorState error={me.error} onRetry={() => me.refetch()} />;
  if (!me.data) return <Loading label="Redirecting" />;
  return (
    <main id="main" className="mx-auto max-w-2xl space-y-4 p-4">
      <h1 className="text-2xl font-bold">Choose a workspace and project</h1>
      {me.data.demo_mode && <p className="text-sm text-muted">Demo mode is on.</p>}
      <Card title="Workspace">
        <div className="flex flex-wrap gap-2">
          {me.data.workspaces.map((w) => (
            <Button key={w.id} variant={ws === w.id ? "primary" : "secondary"} onClick={() => setWs(w.id)} aria-pressed={ws === w.id}>
              {w.name} ({w.role})
            </Button>
          ))}
        </div>
      </Card>
      <Card title="Projects">
        {projects.isLoading && <Loading />}
        {projects.isError && <ErrorState error={projects.error} onRetry={() => projects.refetch()} />}
        {projects.data?.length === 0 && <Empty title="No projects yet" hint="An admin can load the fictional demo project, or a manager can create one." />}
        <ul className="space-y-2">
          {projects.data?.map((p) => (
            <li key={p.id}>
              <Button
                variant="secondary"
                className="w-full text-left"
                onClick={() => {
                  sel.set({ workspaceId: ws, projectId: p.id, sprintId: null });
                  router.push("/dashboard");
                }}
              >
                <strong>{p.key}</strong> — {p.name}
              </Button>
            </li>
          ))}
        </ul>
        {ws !== null && projects.data?.length === 0 && <CreateFirst ws={ws} onDone={() => projects.refetch()} />}
      </Card>
    </main>
  );
}

function CreateFirst({ ws, onDone }: { ws: number; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  async function demo() {
    try {
      await api(`/workspaces/${ws}/integrations/demo`, { method: "POST" });
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    }
  }
  return (
    <div className="mt-3 space-y-2">
      <Button onClick={demo}>Load fictional demo project (admin)</Button>
      {err && (
        <p role="alert" className="text-sm text-danger">
          {err}
        </p>
      )}
    </div>
  );
}
