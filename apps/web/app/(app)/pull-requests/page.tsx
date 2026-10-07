"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PR } from "@/lib/api";
import { usePaths } from "@/components/shell";
import { Badge, Empty, ErrorState, Field, inputCls, Loading, Table, Td } from "@/components/ui";

export default function PullRequestsPage() {
  const { proj } = usePaths();
  const [repo, setRepo] = useState("");
  const [state, setState] = useState("open");
  const [review, setReview] = useState("");
  const [checks, setChecks] = useState("");
  const params = new URLSearchParams();
  if (repo) params.set("repo", repo);
  if (state) params.set("state", state);
  if (review) params.set("review_state", review);
  if (checks) params.set("checks_state", checks);
  const q = useQuery({ queryKey: ["prs", proj, params.toString()], queryFn: () => api<{ items: PR[]; repos: string[] }>(`${proj}/pull-requests?${params}`) });

  const sel = (label: string, value: string, set: (v: string) => void, opts: string[], all = "All") => (
    <Field label={label}>
      <select className={inputCls} value={value} onChange={(e) => set(e.target.value)}>
        <option value="">{all}</option>
        {opts.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    </Field>
  );
  return (
    <>
      <h1 className="text-2xl font-bold">Pull requests</h1>
      <div className="flex flex-wrap items-end gap-3">
        {sel("Repository", repo, setRepo, q.data?.repos ?? [])}
        {sel("State", state, setState, ["open", "merged", "closed"])}
        {sel("Review", review, setReview, ["awaiting_review", "approved", "changes_requested"])}
        {sel("Checks", checks, setChecks, ["passing", "failing", "pending", "none"])}
      </div>
      {q.isLoading && <Loading label="Loading pull requests" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && <Empty title="No pull requests match these filters" />}
      {q.data && q.data.items.length > 0 && (
        <Table head={["PR", "Title", "Author", "Review", "Checks", "Issue"]}>
          {q.data.items.map((p) => (
            <tr key={p.id}>
              <Td>
                <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                  {p.repo}#{p.number}
                </a>
              </Td>
              <Td>
                {p.title} {p.draft && <Badge>draft</Badge>}
              </Td>
              <Td>{p.author ?? "unknown"}</Td>
              <Td>
                <Badge kind={p.review_state === "approved" ? "ok" : p.review_state === "changes_requested" ? "high" : "medium"}>{p.review_state.replace("_", " ")}</Badge>
              </Td>
              <Td>
                <Badge kind={p.checks_state === "failing" ? "high" : p.checks_state === "passing" ? "ok" : "low"}>{p.checks_state}</Badge>
                <ul className="mt-1 text-xs text-muted">
                  {p.checks.map((c) => (
                    <li key={c.name}>
                      {c.name}: {c.conclusion ?? c.status}
                      {c.required ? " (required)" : ""}
                    </li>
                  ))}
                </ul>
              </Td>
              <Td>{p.issue_key ?? "—"}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
