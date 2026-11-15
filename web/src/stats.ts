// 件数の集計（app.py の Units / aggregate_by / wilson_interval と同じ計算）
import { compareValues, type Table, type Value } from "./data";

export type Unit = "row" | "report";

export type AggRow = {
  key: Value[];
  label: string;
  n: number; // 全文書数
  k: number; // 一致件数
  rate: number; // 一致率
  lo: number; // Wilson 95% 下限
  hi: number; // 上限
  ratio: number; // 全体比
  expected: number; // 期待件数
  resid: number; // 標準化残差
};

export type Aggregate = { rows: AggRow[]; p0: number; nUnits: number; nHit: number; isReport: boolean };

export function wilson(k: number, n: number, z = 1.96): [number, number] {
  const p = k / n;
  const denom = 1 + (z * z) / n;
  const center = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [center - half, center + half];
}

/** 単位（文 or レポート）ごとの「先頭行」と「一致したか」。絞り込み後の行だけを対象にする。 */
export function buildUnits(t: Table, mask: Uint8Array | null, hits: Int32Array, unit: Unit) {
  const hitRow = new Uint8Array(t.n);
  for (const i of hits) hitRow[i] = 1;
  const first: number[] = [];
  const hit: number[] = [];
  if (unit === "report" && t.reportId) {
    const seen = new Map<number, number>();
    for (let i = 0; i < t.n; i++) {
      if (mask && !mask[i]) continue;
      const r = t.reportId[i];
      let u = seen.get(r);
      if (u === undefined) {
        u = first.length;
        seen.set(r, u);
        first.push(i);
        hit.push(0);
      }
      if (hitRow[i]) hit[u] = 1;
    }
  } else {
    for (let i = 0; i < t.n; i++) {
      if (mask && !mask[i]) continue;
      first.push(i);
      hit.push(hitRow[i]);
    }
  }
  return { first, hit, isReport: unit === "report" && !!t.reportId };
}

export function aggregate(t: Table, mask: Uint8Array | null, hits: Int32Array, unit: Unit, cols: string[]): Aggregate {
  const u = buildUnits(t, mask, hits, unit);
  const nUnits = u.first.length;
  let nHit = 0;
  for (const h of u.hit) nHit += h;
  const p0 = nUnits ? nHit / nUnits : 0;
  const groups = new Map<string, { key: Value[]; n: number; k: number }>();
  for (let j = 0; j < nUnits; j++) {
    const row = u.first[j];
    const key = cols.map((c) => t.value(c, row));
    if (key.some((v) => v === null)) continue; // pandas の groupby と同じく欠損は除く
    const id = JSON.stringify(key);
    let g = groups.get(id);
    if (!g) groups.set(id, (g = { key, n: 0, k: 0 }));
    g.n++;
    g.k += u.hit[j];
  }
  const rows: AggRow[] = [...groups.values()].map(({ key, n, k }) => {
    const rate = k / n;
    const [lo, hi] = wilson(k, n);
    const expected = n * p0;
    return {
      key, label: key.join(" / "), n, k, rate, lo, hi,
      ratio: rate / p0,
      expected,
      resid: (k - expected) / Math.sqrt(n * p0 * (1 - p0)),
    };
  });
  rows.sort((a, b) => {
    for (let i = 0; i < cols.length; i++) {
      const d = compareValues(cols[i], a.key[i], b.key[i]);
      if (d) return d;
    }
    return 0;
  });
  return { rows, p0, nUnits, nHit, isReport: u.isReport };
}

export const COUNT_SORTS = [
  { id: "axis", label: "軸の順" },
  { id: "rate", label: "一致率" },
  { id: "ratio", label: "全体比" },
  { id: "resid", label: "残差の大きさ" },
  { id: "k", label: "一致件数" },
] as const;
export type CountSort = (typeof COUNT_SORTS)[number]["id"];

export function sortRows(rows: AggRow[], by: CountSort): AggRow[] {
  const r = [...rows];
  const num = (x: number) => (Number.isFinite(x) ? x : -Infinity);
  if (by === "rate") r.sort((a, b) => num(b.rate) - num(a.rate));
  else if (by === "ratio") r.sort((a, b) => num(b.ratio) - num(a.ratio));
  else if (by === "resid") r.sort((a, b) => num(Math.abs(b.resid)) - num(Math.abs(a.resid)));
  else if (by === "k") r.sort((a, b) => b.k - a.k);
  return r;
}

export const METRICS = [
  { id: "k", label: "一致件数", fmt: (v: number) => v.toLocaleString(), mid: null },
  { id: "rate", label: "一致率", fmt: (v: number) => `${Math.round(v * 100)}%`, mid: null },
  { id: "n", label: "全文書数", fmt: (v: number) => v.toLocaleString(), mid: null },
  { id: "ratio", label: "全体比", fmt: (v: number) => v.toFixed(1), mid: 1 },
  { id: "resid", label: "標準化残差", fmt: (v: number) => v.toFixed(1), mid: 0 },
] as const;
export type Metric = (typeof METRICS)[number]["id"];

export function toCsv(cols: string[], rows: AggRow[]): string {
  const head = [...cols, "一致件数", "全文書数", "一致率", "信頼区間 下限", "信頼区間 上限", "全体比", "期待件数", "標準化残差"];
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [head.map(esc).join(",")];
  for (const r of rows) {
    lines.push([...r.key, r.k, r.n, r.rate, r.lo, r.hi, r.ratio, r.expected, r.resid].map(esc).join(","));
  }
  return "﻿" + lines.join("\n");
}
