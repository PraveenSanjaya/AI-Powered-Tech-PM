"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useSelection } from "@/lib/context";
import { usePaths } from "./shell";
import { Field, inputCls } from "./ui";

export type Sprint = { id: number; name: string; goal: string; start_date: string; end_date: string; state: string };

export function useSprints() {
  const { proj } = usePaths();
  return useQuery({ queryKey: ["sprints", proj], queryFn: () => api<Sprint[]>(`${proj}/sprints`) });
}

export function SprintPicker() {
  const { sprintId, set } = useSelection();
  const sprints = useSprints();
  return (
    <Field label="Sprint">
      <select className={inputCls} value={sprintId ?? ""} onChange={(e) => set({ sprintId: e.target.value ? Number(e.target.value) : null })}>
        <option value="">Active sprint</option>
        {sprints.data?.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} ({s.state})
          </option>
        ))}
      </select>
    </Field>
  );
}
