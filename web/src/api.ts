// サーバー（FastAPI）との通信
import type { Filters, Mode } from "./data";

export type HistoryEntry = {
  query: string; query2: string; mode: Mode | ""; exclude: string; dataset: string; hits: number; at: string;
};
export type History = { users: Record<string, HistoryEntry[]> };

async function post<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `通信に失敗しました（${r.status}）`);
  return r.json();
}

export const api = {
  history: (): Promise<History> => fetch("/api/history").then((r) => r.json()),
  addUser: (name: string) => post<History>("/api/history/user", { name }),
  record: (e: Omit<HistoryEntry, "at"> & { user: string }) => post<History>("/api/history/record", e),
  removeEntry: (user: string, e: HistoryEntry) => post<History>("/api/history/remove", { user, at: e.at, query: e.query }),
  clearHistory: (user: string) => post<History>("/api/history/clear", { user }),
  capabilities: (): Promise<{ janome: boolean }> => fetch("/api/capabilities").then((r) => r.json()),
  async upload(file: File): Promise<{ id: string }> {
    const r = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "アップロードに失敗しました。");
    return r.json();
  },
  terms: (spec: Spec, signal?: AbortSignal) => post<TermsResult>("/api/terms", spec, signal),
  groupTerms: (spec: Spec, signal?: AbortSignal) => post<GroupTermsResult>("/api/group_terms", spec, signal),
  context: (spec: Spec, term: string, group: string | null, signal?: AbortSignal) =>
    post<{ n_units: number; rows: number[] }>("/api/term_context", { spec, term, group }, signal),
};

/** サーバーで同じ検索・絞り込みを再現するための条件。 */
export type Spec = {
  dataset: string; terms: string[]; mode: Mode; excl: string[];
  year: Filters["year"]; sel: Filters["sel"];
  unit: "row" | "report"; method: "fast" | "janome"; rank_by: "count" | "score";
  min_count: number; top_n: number; max_docs: number; exclude_words: string[];
  group_cols?: string[];
};

export type TermRow = { term: string; docs: number; rate: number; bg_rate: number; lift: number; g2: number };
export type TermsResult = { unit: string; n_units: number; n_hit_units: number; n_fg: number; n_bg: number; terms: TermRow[] };
export type GroupTermsResult = {
  unit: string; n_units: number; n_hit_units: number; n_fg: number; group_cols: string[];
  groups: { label: string; n: number; terms: TermRow[] }[];
  skipped: { label: string; n: number }[];
};
