"use client";
import { createContext, useContext, useEffect, useState } from "react";

type Sel = { workspaceId: number | null; projectId: number | null; sprintId: number | null };
type Ctx = Sel & { set: (s: Partial<Sel>) => void; ready: boolean };
const C = createContext<Ctx>({ workspaceId: null, projectId: null, sprintId: null, set: () => {}, ready: false });
const KEY = "techpm.selection";

export function SelectionProvider({ children }: { children: React.ReactNode }) {
  const [sel, setSel] = useState<Sel>({ workspaceId: null, projectId: null, sprintId: null });
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setSel(JSON.parse(raw));
    } catch {
      /* storage unavailable: start without a saved selection */
    }
    setReady(true);
  }, []);
  const set = (s: Partial<Sel>) =>
    setSel((prev) => {
      const next = { ...prev, ...s };
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  return <C.Provider value={{ ...sel, set, ready }}>{children}</C.Provider>;
}
export const useSelection = () => useContext(C);
