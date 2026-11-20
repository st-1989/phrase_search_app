// データの読み込みと完全一致検索（app.py の find_hits / mask_excluded / build_snippet と同じ挙動）

export type Value = string | number | null;
type EncodedColumn = { values: Value[]; codes: number[] } | { raw: Value[] };
/** 複数ファイルをまとめたときの、ファイルごとの行数と、前のファイルと重複して除いた行数。 */
export type SourceInfo = { name: string; rows: number; dropped: number };
type Payload = { rows: number; columns: string[]; data: Record<string, EncodedColumn>; sources?: SourceInfo[] };

export const TEXT_COLUMN = "本文";
export const YEAR_COLUMN = "年(CDEX)";
export const RATING_COLUMN = "発行体格付(業務)";
/** 複数ファイルをまとめたときだけある列（行の元のファイル名）。 */
export const SOURCE_COLUMN = "ファイル";
/** 複数ファイルをまとめたデータセットの id は、各ファイルの id をこれでつなぐ（server/main.py と同じ）。 */
export const DATASET_SEP = "|";
/** 前回のレポートと比べた変化（server/merge.py の add_change_columns が作る列）。 */
export const PREV_RATING_COLUMN = "前回格付";
export const RATING_CHANGE_COLUMN = "格付の変化";
export const OUTLOOK_CHANGE_COLUMN = "方向性の変化";
export const FILTER_COLUMNS = [
  SOURCE_COLUMN,
  RATING_COLUMN,
  RATING_CHANGE_COLUMN,
  OUTLOOK_CHANGE_COLUMN,
  "業種(業務)",
  "担当部署(業務)",
  "担当アナリスト(CDEX)",
  "格付アクション(業務)",
];
export const FILTER_LABELS: Record<string, string> = {
  [YEAR_COLUMN]: "年",
  [SOURCE_COLUMN]: "ファイル",
  [RATING_COLUMN]: "格付",
  [RATING_CHANGE_COLUMN]: "格付の変化",
  [OUTLOOK_CHANGE_COLUMN]: "方向性の変化",
  "業種(業務)": "業種",
  "担当部署(業務)": "担当部署",
  "担当アナリスト(CDEX)": "担当",
  "格付アクション(業務)": "アクション",
};
export const DEFAULT_YEAR_FROM = 2016;
export const DEFAULT_YEAR_TO = 2026;
const RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
  "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"];
/** 値の並びを決めている列（それ以外は文字順）。 */
const VALUE_ORDERS: Record<string, string[]> = {
  [RATING_COLUMN]: RATING_ORDER,
  [RATING_CHANGE_COLUMN]: ["引き上げ", "引き下げ", "据え置き", "前回なし"],
  [OUTLOOK_CHANGE_COLUMN]: ["上向き", "下向き", "その他の変更", "変化なし", "前回なし"],
};

/** 列は「値の一覧 values」と「各行がどの値か codes（-1 は欠損）」で持つ。絞り込みは codes の比較だけで済む。 */
export type Col = { values: Value[]; codes: Int32Array };

export class Table {
  readonly n: number;
  readonly columns: string[];
  readonly cols: Record<string, Col> = {};
  readonly text: string[];
  /** レポート（発行体ID＋公表日）の番号。レポートを識別できなければ null。 */
  readonly reportId: Int32Array | null;
  readonly sources: SourceInfo[];

  constructor(p: Payload) {
    this.n = p.rows;
    this.sources = p.sources ?? [];
    this.columns = p.columns;
    for (const c of p.columns) {
      const e = p.data[c];
      if ("raw" in e) {
        this.cols[c] = { values: e.raw, codes: Int32Array.from({ length: e.raw.length }, (_, i) => i) };
      } else {
        this.cols[c] = { values: e.values, codes: Int32Array.from(e.codes) };
      }
    }
    const t = this.cols[TEXT_COLUMN];
    this.text = Array.from(t.codes, (k) => (k < 0 ? "" : String(t.values[k])));
    this.reportId = this.buildReportId();
  }

  has(col: string) {
    return col in this.cols;
  }

  value(col: string, i: number): Value {
    const c = this.cols[col];
    if (!c) return null;
    const k = c.codes[i];
    return k < 0 ? null : c.values[k];
  }

  private buildReportId(): Int32Array | null {
    const id = this.cols["発行体ID(業務)"];
    const date = this.cols["公表日(業務)"] ?? this.cols["公表日(CDEX)"];
    if (!id || !date) return null;
    const map = new Map<string, number>();
    const out = new Int32Array(this.n);
    for (let i = 0; i < this.n; i++) {
      const key = `${id.codes[i]}|${date.codes[i]}`;
      let r = map.get(key);
      if (r === undefined) map.set(key, (r = map.size));
      out[i] = r;
    }
    return out;
  }

  /** 絞り込みの選択肢（格付は AAA→D、変化は 引き上げ→… の順、それ以外は文字順）。 */
  options(col: string): Value[] {
    const c = this.cols[col];
    if (!c) return [];
    const used = new Set<number>();
    for (const k of c.codes) if (k >= 0) used.add(k);
    const vals = [...used].map((k) => c.values[k]);
    const order = VALUE_ORDERS[col];
    if (order) {
      const rank = (v: Value) => {
        const i = order.indexOf(String(v));
        return i < 0 ? 99 : i;
      };
      return vals.sort((a, b) => rank(a) - rank(b) || String(a).localeCompare(String(b)));
    }
    return vals.sort((a, b) =>
      typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "ja"),
    );
  }

  yearBounds(): [number, number] | null {
    const c = this.cols[YEAR_COLUMN];
    if (!c) return null;
    const ys = c.values.filter((v): v is number => typeof v === "number");
    if (!ys.length) return null;
    return [Math.min(...ys), Math.max(...ys)];
  }
}

export async function fetchDatasets(): Promise<{ id: string; size: number }[]> {
  const r = await fetch("/api/datasets");
  if (!r.ok) throw new Error("データセットの一覧を取得できませんでした。");
  return r.json();
}

export async function fetchTable(id: string): Promise<Table> {
  const r = await fetch(`/api/dataset?id=${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "読み込みに失敗しました。");
  return new Table(await r.json());
}

// ---- 絞り込み ----

export type Filters = { year: [number, number] | null; sel: Record<string, Value[]> };

export function filterMask(t: Table, f: Filters): Uint8Array | null {
  const active = Object.entries(f.sel).filter(([c, v]) => v.length && t.has(c));
  if (!f.year && !active.length) return null;
  const mask = new Uint8Array(t.n).fill(1);
  if (f.year && t.has(YEAR_COLUMN)) {
    const [lo, hi] = f.year;
    const c = t.cols[YEAR_COLUMN];
    for (let i = 0; i < t.n; i++) {
      const k = c.codes[i];
      const y = k < 0 ? null : (c.values[k] as number);
      if (y === null || y < lo || y > hi) mask[i] = 0;
    }
  }
  for (const [col, chosen] of active) {
    const c = t.cols[col];
    const ok = new Uint8Array(c.values.length);
    const set = new Set(chosen);
    c.values.forEach((v, k) => set.has(v) && (ok[k] = 1));
    for (let i = 0; i < t.n; i++) {
      const k = c.codes[i];
      if (k < 0 || !ok[k]) mask[i] = 0;
    }
  }
  return mask;
}

// ---- 検索 ----

export const MODES = [
  { id: "row", label: "同じ文に両方", hint: "1つの文に両方の語が入る" },
  { id: "report", label: "同じレポートに両方", hint: "別の文でも、同じレポートに両方の語が入る" },
  { id: "or", label: "どちらか", hint: "どちらか一方でも含む" },
] as const;
export type Mode = (typeof MODES)[number]["id"];

/** 除外フレーズに当たる部分を同じ長さの \0 に置き換える（位置はずらさない）。 */
export function maskExcluded(text: string, excl: string[]): string {
  for (const ph of excl) if (ph) text = text.split(ph).join("\0".repeat(ph.length));
  return text;
}

export function sortExcl(excl: string[]) {
  return [...excl].filter(Boolean).sort((a, b) => b.length - a.length);
}

/** 一致した行の番号（元の順）を返す。 */
export function findHits(t: Table, terms: string[], mode: Mode, excl: string[], mask: Uint8Array | null): Int32Array {
  const ex = sortExcl(excl);
  const n = t.n;
  const a = new Uint8Array(n);
  const b = new Uint8Array(n);
  const two = terms.length === 2;
  for (let i = 0; i < n; i++) {
    if (mask && !mask[i]) continue;
    const s = ex.length ? maskExcluded(t.text[i], ex) : t.text[i];
    if (s.includes(terms[0])) a[i] = 1;
    if (two && s.includes(terms[1])) b[i] = 1;
  }
  const out: number[] = [];
  if (!two) {
    for (let i = 0; i < n; i++) if (a[i]) out.push(i);
  } else if (mode === "or") {
    for (let i = 0; i < n; i++) if (a[i] || b[i]) out.push(i);
  } else if (mode === "row" || !t.reportId) {
    for (let i = 0; i < n; i++) if (a[i] && b[i]) out.push(i);
  } else {
    const rid = t.reportId;
    let m = 0;
    for (let i = 0; i < n; i++) if (rid[i] >= m) m = rid[i] + 1;
    const ha = new Uint8Array(m);
    const hb = new Uint8Array(m);
    for (let i = 0; i < n; i++) {
      if (a[i]) ha[rid[i]] = 1;
      if (b[i]) hb[rid[i]] = 1;
    }
    for (let i = 0; i < n; i++) if ((a[i] || b[i]) && ha[rid[i]] && hb[rid[i]]) out.push(i);
  }
  return Int32Array.from(out);
}

// ---- ハイライト ----

/** term: 何番目の検索語か（0 / 1）。-1 は一致でない部分。 */
export type Piece = { s: string; term: number };

export function spansOf(text: string, terms: string[], excl: string[]): [number, number, number][] {
  const masked = maskExcluded(text, sortExcl(excl));
  const spans: [number, number, number][] = [];
  terms.forEach((q, ti) => {
    if (!q) return;
    let from = 0;
    for (;;) {
      const at = masked.indexOf(q, from);
      if (at < 0) break;
      spans.push([at, at + q.length, ti]);
      from = at + Math.max(1, q.length);
    }
  });
  spans.sort((x, y) => x[0] - y[0] || y[1] - x[1]);
  const merged: [number, number, number][] = [];
  for (const sp of spans) {
    const last = merged[merged.length - 1];
    if (last && sp[0] <= last[1]) last[1] = Math.max(last[1], sp[1]);
    else merged.push([...sp]);
  }
  return merged;
}

/** 一致の前後 context 文字だけを切り出した断片。full=true なら全文。 */
export function pieces(text: string, spans: [number, number, number][], context: number, full: boolean) {
  const lo = full || !spans.length ? 0 : Math.max(0, spans[0][0] - context);
  const hi = full || !spans.length ? text.length : Math.min(text.length, spans[spans.length - 1][1] + context);
  const out: Piece[] = [];
  let cur = lo;
  for (const [s, e, ti] of spans) {
    if (e <= lo || s >= hi) continue;
    const ss = Math.max(s, lo);
    if (ss > cur) out.push({ s: text.slice(cur, ss), term: -1 });
    out.push({ s: text.slice(ss, Math.min(e, hi)), term: ti });
    cur = Math.min(e, hi);
  }
  if (cur < hi) out.push({ s: text.slice(cur, hi), term: -1 });
  return { pieces: out, before: lo > 0, after: hi < text.length };
}

// ---- 結果の並び ----

/** 格付の順位（AAA が 0、不明は 99）。 */
function ratingRank(v: Value): number {
  const i = RATING_ORDER.indexOf(String(v ?? ""));
  return i < 0 ? 99 : i;
}

/**
 * 結果の並び: 年の新しい順 → 格付の高い順（AAA→D）→ 業種（文字順）→ 公表日の新しい順 → 発行体名。
 * 同じレポートの文は元の並び（文の順）を保つ。
 */
export function sortHits(t: Table, rows: Int32Array | number[]): number[] {
  const year = t.cols[YEAR_COLUMN];
  const rating = t.cols[RATING_COLUMN];
  const ind = t.cols["業種(業務)"];
  const date = t.cols["公表日(CDEX)"];
  const issuer = t.cols["発行体名(CDEX)"];
  const val = (c: Col | undefined, i: number): Value => (c && c.codes[i] >= 0 ? c.values[c.codes[i]] : null);
  const str = (v: Value) => (v === null ? "" : String(v));
  const cmpStr = (a: string, b: string) => (a === b ? 0 : a === "" ? 1 : b === "" ? -1 : a < b ? -1 : 1);
  return Array.from(rows).sort((a, b) => {
    const ya = val(year, a), yb = val(year, b);
    if (ya !== yb) {
      if (ya === null) return 1;
      if (yb === null) return -1;
      const d = Number(yb) - Number(ya);
      if (d) return d;
    }
    const r = ratingRank(val(rating, a)) - ratingRank(val(rating, b));
    if (r) return r;
    const c = cmpStr(str(val(ind, a)), str(val(ind, b)));
    if (c) return c;
    const da = str(val(date, a)), db = str(val(date, b));
    if (da !== db) return da === "" ? 1 : db === "" ? -1 : da < db ? 1 : -1;
    const n = cmpStr(str(val(issuer, a)), str(val(issuer, b)));
    if (n) return n;
    return a - b;
  });
}

// ---- 集計の軸 ----

export const GROUP_COLUMNS = [RATING_COLUMN, "格付の方向性(業務)", "格付アクション(業務)", RATING_CHANGE_COLUMN,
  OUTLOOK_CHANGE_COLUMN, "業種(業務)", YEAR_COLUMN];
export const GROUP_LABELS: Record<string, string> = {
  [RATING_COLUMN]: "格付",
  [RATING_CHANGE_COLUMN]: "格付の変化",
  [OUTLOOK_CHANGE_COLUMN]: "方向性の変化",
  "格付の方向性(業務)": "方向性",
  "格付アクション(業務)": "アクション",
  "業種(業務)": "業種",
  [YEAR_COLUMN]: "年",
};

/** 軸の値の並び（格付は AAA→D、変化は 引き上げ→…、数値は小さい順、文字は pandas と同じ文字コード順）。 */
export function compareValues(col: string, a: Value, b: Value): number {
  const order = VALUE_ORDERS[col];
  if (order) {
    const ra = order.indexOf(String(a));
    const rb = order.indexOf(String(b));
    const d = (ra < 0 ? 99 : ra) - (rb < 0 ? 99 : rb);
    if (d) return d;
  }
  if (typeof a === "number" && typeof b === "number") return a - b;
  const sa = String(a);
  const sb = String(b);
  return sa < sb ? -1 : sa > sb ? 1 : 0;
}

export function splitDataset(id: string): string[] {
  return id.split(DATASET_SEP).filter(Boolean);
}

export function fileLabel(id: string) {
  return id.startsWith(".cache/uploads/") ? "アップロード: " + id.slice(".cache/uploads/".length) : id;
}

export function datasetLabel(id: string) {
  return splitDataset(id).map(fileLabel).join(" ＋ ");
}
