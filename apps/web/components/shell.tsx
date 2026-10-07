"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { api, ApiError, setCsrf, type Me } from "@/lib/api";
import { useSelection } from "@/lib/context";
import { Loading } from "./ui";

const NAV = [
  ["/dashboard", "Dashboard"],
  ["/sprint", "Sprint detail"],
  ["/issues", "Issues"],
  ["/pull-requests", "Pull requests"],
  ["/risks", "Risks & blockers"],
  ["/assistant", "AI assistant"],
  ["/actions", "Action approvals"],
  ["/integrations", "Integrations"],
  ["/automation", "Automation"],
  ["/audit", "Audit history"],
] as const;

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await api<Me>("/auth/me");
      setCsrf(me.csrf_token);
      return me;
    },
  });
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const path = usePathname();
  const sel = useSelection();
  const qc = useQueryClient();

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.error, router]);
  useEffect(() => {
    if (me.data && sel.ready && (!sel.workspaceId || !sel.projectId)) router.replace("/select");
  }, [me.data, sel.ready, sel.workspaceId, sel.projectId, router]);

  if (me.isLoading || !sel.ready) return <Loading label="Loading session" />;
  if (!me.data) return <Loading label="Redirecting to sign in" />;
  const role = me.data.workspaces.find((w) => w.id === sel.workspaceId)?.role;

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <nav aria-label="Main" className="border-b border-border bg-surface p-3 md:w-56 md:shrink-0 md:border-b-0 md:border-r">
        <p className="mb-3 text-sm font-bold">AI-Powered Tech PM</p>
        <ul className="flex flex-wrap gap-1 md:flex-col">
          {NAV.map(([href, label]) => (
            <li key={href}>
              <Link href={href} aria-current={path === href ? "page" : undefined} className={`block rounded px-2 py-1.5 text-sm ${path === href ? "bg-accent text-accent-fg" : "hover:bg-bg"}`}>
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 flex-1">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-surface px-4 py-2 text-sm">
          <div>
            <Link href="/select" className="underline">
              Switch workspace / project
            </Link>
            <span className="ml-3 text-muted">
              {me.data.user.name} · role: <strong>{role ?? "?"}</strong>
            </span>
          </div>
          <button onClick={logout} className="rounded border border-border px-2 py-1 hover:bg-bg">
            Sign out
          </button>
        </header>
        {me.data.demo_mode && (
          <div role="status" className="border-b border-purple-400 bg-purple-100 px-4 py-1.5 text-xs text-purple-900 dark:bg-purple-950 dark:text-purple-200">
            DEMO MODE — fictional data and simulated integrations. Approved actions are recorded locally and are never sent to Jira, Slack or any real service.
          </div>
        )}
        <main id="main" className="mx-auto max-w-6xl space-y-4 p-4">
          {children}
        </main>
      </div>
    </div>
  );
}

/** Convenience: current role + helpers for pages. */
export function useRole() {
  const me = useMe();
  const { workspaceId } = useSelection();
  const role = me.data?.workspaces.find((w) => w.id === workspaceId)?.role;
  return { role, isManager: role === "manager" || role === "admin", isAdmin: role === "admin", me: me.data };
}

export function usePaths() {
  const { workspaceId, projectId } = useSelection();
  return { ws: `/workspaces/${workspaceId}`, proj: `/workspaces/${workspaceId}/projects/${projectId}`, workspaceId, projectId };
}
