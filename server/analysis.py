"""共起語の分析（app.py の enriched_terms / Units / doc_token_sets と同じ計算）。

画面から「検索条件（spec）」を受け取り、サーバー側で同じ絞り込み・一致判定をやり直してから解析する。
語の切り出し結果は文ごとに記憶し、同じ文は再解析しない。
"""
from __future__ import annotations

import re
import threading
from collections import Counter, OrderedDict

import numpy as np
import pandas as pd

from .merge import OUTLOOK_CHANGE_COLUMN, OUTLOOK_CHANGE_ORDER, RATING_CHANGE_COLUMN, RATING_CHANGE_ORDER

TEXT_COLUMN = "本文"
YEAR_COLUMN = "年(CDEX)"
RATING_COLUMN = "発行体格付(業務)"
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
                "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"]

# 値の並びを決めている列（それ以外は文字順）
VALUE_ORDERS = {
    RATING_COLUMN: RATING_ORDER,
    RATING_CHANGE_COLUMN: RATING_CHANGE_ORDER,
    OUTLOOK_CHANGE_COLUMN: OUTLOOK_CHANGE_ORDER,
}
_RATING_SYMBOL = re.compile(r"(?:AAA|AA|A|BBB|BB|B|CCC|CC|C|D)[+-]?")
_RUN_PATTERN = re.compile(r"[一-龥々〆]{2,}|[ァ-ヴー]{2,}|[A-Za-z][A-Za-z&/\-]+")
_WORD_CHARS = re.compile(r"[一-龥々ァ-ヴーぁ-んA-Za-z0-9]+")
_JANOME_NOUN = {"一般", "固有名詞", "サ変接続", "形容動詞語幹"}

try:
    from janome.tokenizer import Tokenizer as _JanomeTokenizer
except ImportError:  # pragma: no cover
    _JanomeTokenizer = None

JANOME_AVAILABLE = _JanomeTokenizer is not None
_janome_instance = None
_janome_lock = threading.Lock()
_token_memo: dict = {}


def _janome():
    global _janome_instance
    if _janome_instance is None:
        _janome_instance = _JanomeTokenizer()
    return _janome_instance


def _tokens_fast(text: str) -> frozenset:
    return frozenset(_RUN_PATTERN.findall(text))


def _tokens_janome(text: str) -> frozenset:
    out = set()
    with _janome_lock:  # Janome はスレッドセーフではない
        toks = list(_janome().tokenize(text))
    for tok in toks:
        pos = tok.part_of_speech.split(",")
        if (pos[0] == "名詞" and pos[1] in _JANOME_NOUN and len(tok.surface) >= 2
                and not tok.surface.isdigit() and _WORD_CHARS.fullmatch(tok.surface)):
            out.add(tok.surface)
    return frozenset(out)


def doc_token_sets(texts, method: str, remove: tuple = (), mask: tuple = ()) -> list[frozenset]:
    fn = _tokens_janome if method == "janome" and JANOME_AVAILABLE else _tokens_fast
    out = []
    for text in texts:
        key = (method, hash(text), remove, mask)
        hit = _token_memo.get(key)
        if hit is None:
            t = str(text)
            for ph in mask:
                t = t.replace(ph, " ")
            for q in remove:
                t = t.replace(q, " ")
            hit = _token_memo[key] = fn(t)
        out.append(hit)
    return out


# ---- 検索条件の再現（画面の data.ts と同じ） ----

MODE_LABELS = {"row": "両方を含む（同じ文）", "report": "両方を含む（同じレポート内）", "or": "どちらかを含む"}


def report_key_columns(df: pd.DataFrame) -> list[str] | None:
    if "発行体ID(業務)" not in df.columns:
        return None
    for date_col in ("公表日(業務)", "公表日(CDEX)"):
        if date_col in df.columns:
            return ["発行体ID(業務)", date_col]
    return None


def apply_filters(df: pd.DataFrame, year, sel: dict) -> pd.DataFrame:
    mask = np.ones(len(df), bool)
    if year and YEAR_COLUMN in df.columns:
        mask &= df[YEAR_COLUMN].between(int(year[0]), int(year[1])).fillna(False).to_numpy()
    for col, values in (sel or {}).items():
        if values and col in df.columns:
            mask &= df[col].isin(values).to_numpy()
    return df if mask.all() else df[mask]


def find_hits(df: pd.DataFrame, terms: list[str], mode: str, excl: tuple) -> np.ndarray:
    col = df[TEXT_COLUMN]
    for ph in sorted(excl, key=len, reverse=True):
        col = col.str.replace(ph, "\x00" * len(ph), regex=False)
    masks = [col.str.contains(t, regex=False, na=False).to_numpy() for t in terms]
    if not masks:
        return np.zeros(len(df), dtype=bool)
    if len(masks) == 1:
        return masks[0]
    any_hit = np.logical_or.reduce(masks)
    report_cols = report_key_columns(df)
    if mode == "or":
        return any_hit
    if mode == "row" or not report_cols:
        return np.logical_and.reduce(masks)
    rid = df.groupby(report_cols, sort=False, observed=True, dropna=False).ngroup().to_numpy()
    n = int(rid.max()) + 1 if len(rid) else 0
    has_all = np.ones(n, bool)
    for mask in masks:
        present = np.zeros(n, bool)
        present[rid[mask]] = True
        has_all &= present
    return has_all[rid] & any_hit


class Units:
    """集計の単位（文 or レポート）。app.py の Units と同じ。"""

    def __init__(self, df: pd.DataFrame, hit: np.ndarray, report_cols: list[str] | None):
        if report_cols is None:
            self.n = len(df)
            self.hit = hit
            self.first_idx = np.arange(self.n)
            self.order = self.first_idx
            self.counts = np.ones(self.n, dtype=int)
        else:
            rid = df.groupby(report_cols, sort=False, observed=True, dropna=False).ngroup().to_numpy()
            self.n = int(rid.max()) + 1 if len(rid) else 0
            self.hit = np.zeros(self.n, dtype=bool)
            self.hit[rid[hit]] = True
            _, self.first_idx = np.unique(rid, return_index=True)
            self.order = np.argsort(rid, kind="stable")
            self.counts = np.bincount(rid, minlength=self.n)
        self.starts = np.concatenate([[0], np.cumsum(self.counts)[:-1]]).astype(int) if self.n else np.array([], int)
        self.is_report = report_cols is not None
        self.row_hit = hit

    def rows(self, u: int) -> np.ndarray:
        return self.order[self.starts[u]: self.starts[u] + self.counts[u]]

    def token_sets(self, texts: pd.Series, units: np.ndarray, method: str,
                   only_hit: bool = False, remove: tuple = (), mask: tuple = ()) -> list[frozenset]:
        pos = [self.rows(u) for u in units]
        if only_hit:
            pos = [p[self.row_hit[p]] for p in pos]
        flat = np.concatenate(pos) if pos else np.array([], int)
        toks = doc_token_sets(texts.iloc[flat], method, remove, mask)
        out, i = [], 0
        for p in pos:
            group = toks[i: i + len(p)]
            out.append(group[0] if len(group) == 1 else frozenset().union(*group))
            i += len(p)
        return out


def sample_positions(positions: np.ndarray, n: int | None, seed: int = 0) -> np.ndarray:
    if not n or len(positions) <= n:
        return positions
    return np.sort(np.random.default_rng(seed).choice(positions, n, replace=False))


def enriched_terms(fg, bg, queries, exclude, min_count, top_n, rank_by) -> pd.DataFrame:
    n_fg, n_bg = len(fg), len(bg)
    fg_count, bg_count = Counter(), Counter()
    for t in fg:
        fg_count.update(t)
    for t in bg:
        bg_count.update(t)
    terms = [
        w for w, c in fg_count.items()
        if c >= min_count and w not in exclude and all(q not in w and w not in q for q in queries)
        and not _RATING_SYMBOL.fullmatch(w)
    ]
    if not terms or n_fg == 0 or n_bg == 0:
        return pd.DataFrame()
    a = np.array([fg_count[w] for w in terms], dtype=float)
    c = np.array([bg_count.get(w, 0) for w in terms], dtype=float)
    b, d = n_fg - a, n_bg - c
    total = n_fg + n_bg
    col1, col2 = a + c, b + d

    def xlogx(o, e):
        with np.errstate(all="ignore"):
            return np.where(o > 0, o * np.log(o / e), 0.0)

    g2 = 2 * (xlogx(a, n_fg * col1 / total) + xlogx(b, n_fg * col2 / total)
              + xlogx(c, n_bg * col1 / total) + xlogx(d, n_bg * col2 / total))
    rate_fg, rate_bg = a / n_fg, c / n_bg
    lift = rate_fg / ((c + 0.5) / (n_bg + 1))
    table = pd.DataFrame({"term": terms, "docs": a.astype(int), "rate": rate_fg,
                          "bg_rate": rate_bg, "lift": lift, "g2": g2})
    # 同点は語の文字順にして、毎回同じ並びにする（app.py は実行ごとに同点の順が変わりうる）
    if rank_by == "score":
        table = table[rate_fg > rate_bg].sort_values(["g2", "term"], ascending=[False, True])
    else:
        table = table.sort_values(["docs", "g2", "term"], ascending=[False, False, True])
    if top_n:
        table = table.head(top_n)
    return table.reset_index(drop=True)


def combo_key(values: tuple, columns: list[str]) -> tuple:
    def key(v, c):
        order = VALUE_ORDERS.get(c)
        if order is None:
            return (0, v)
        return (order.index(v) if v in order else 99, str(v))
    return tuple(key(v, c) for v, c in zip(values, columns))


def _py(v):
    return v.item() if hasattr(v, "item") else v


# ---- 解析の実行（直近の結果を記憶して、語の使用例の表示に使い回す） ----

class Analysis:
    def __init__(self, df: pd.DataFrame, spec: dict):
        self.spec = spec
        terms = [t for t in spec.get("terms", []) if t]
        excl = tuple(sorted({e for e in spec.get("excl", []) if e}, key=len, reverse=True))
        self.queries = terms
        self.excl = excl
        self.df = apply_filters(df, spec.get("year"), spec.get("sel") or {})
        hit = find_hits(self.df, terms, spec.get("mode", "report"), excl)
        report_cols = report_key_columns(self.df)
        use_report = spec.get("unit", "report") == "report" and report_cols is not None
        self.units = Units(self.df, hit, report_cols if use_report else None)
        self.method = spec.get("method", "fast")
        self.remove = tuple(sorted(terms, key=len, reverse=True))
        max_docs = spec.get("max_docs") or None
        hit_units = np.flatnonzero(self.units.hit)
        self.n_hit_units = len(hit_units)
        self.fg_units = sample_positions(hit_units, max_docs)
        self.max_docs = max_docs
        self.texts = self.df[TEXT_COLUMN]
        self.fg = self.units.token_sets(self.texts, self.fg_units, self.method, only_hit=True,
                                        remove=self.remove, mask=excl)
        self.exclude_words = set(spec.get("exclude_words") or [])
        self.min_count = int(spec.get("min_count", 3))
        self.top_n = spec.get("top_n") or None
        self.rank_by = spec.get("rank_by", "count")
        self.groups: dict[str, np.ndarray] = {}

    def _rows(self, t: pd.DataFrame) -> list[dict]:
        return [{k: _py(v) for k, v in r.items()} for r in t.to_dict("records")]

    def terms(self) -> dict:
        bg_units = sample_positions(np.arange(self.units.n), self.max_docs, seed=1)
        bg = self.units.token_sets(self.texts, bg_units, self.method, remove=self.remove, mask=self.excl)
        table = enriched_terms(self.fg, bg, self.queries, self.exclude_words, self.min_count, self.top_n, self.rank_by)
        return {
            "unit": "report" if self.units.is_report else "row",
            "n_units": self.units.n, "n_hit_units": self.n_hit_units,
            "n_fg": len(self.fg_units), "n_bg": len(bg),
            "terms": self._rows(table),
        }

    def group_terms(self, group_cols: list[str]) -> dict:
        group_cols = [c for c in group_cols if c in self.df.columns]
        gdf = self.df[group_cols].iloc[self.units.first_idx[self.fg_units]]
        valid = gdf.notna().all(axis=1).to_numpy()
        combos = list(zip(*[gdf[c].to_numpy() for c in group_cols]))
        groups, skipped = [], []
        for value in sorted({c for c, ok in zip(combos, valid) if ok}, key=lambda v: combo_key(v, group_cols)):
            idx = np.flatnonzero(valid & np.array([c == value for c in combos]))
            label = " / ".join(str(_py(v)) for v in value)
            if len(idx) < max(self.min_count, 3):
                skipped.append({"label": label, "n": int(len(idx))})
                continue
            fg = [self.fg[i] for i in idx]
            table = enriched_terms(fg, self.fg, self.queries, self.exclude_words, self.min_count, self.top_n, self.rank_by)
            self.groups[label] = idx
            groups.append({"label": label, "n": int(len(idx)), "terms": self._rows(table)})
        return {
            "unit": "report" if self.units.is_report else "row",
            "n_units": self.units.n, "n_hit_units": self.n_hit_units, "n_fg": len(self.fg_units),
            "group_cols": group_cols, "groups": groups, "skipped": skipped,
        }

    def context(self, term: str, group: str | None, limit: int = 2000) -> dict:
        """語が検索フレーズと同じ文に出てくる行（元データの行番号）。"""
        idx = self.groups.get(group) if group else None
        ids = self.fg_units if idx is None else self.fg_units[idx]
        sets = self.fg if idx is None else [self.fg[i] for i in idx]
        sel = [int(u) for u, t in zip(ids, sets) if term in t]
        rows: list[int] = []
        for u in sel:
            for pos in sorted(self.units.rows(u)):
                # 絞り込んだ語がすべて同じ文にある、実際に集計した行だけを表示する。
                if not self.units.row_hit[pos]:
                    continue
                text = str(self.texts.iloc[pos])
                masked = text
                for ph in self.excl:
                    masked = masked.replace(ph, "\x00" * len(ph))
                if term in masked and any(q in masked for q in self.queries):
                    rows.append(int(self.df.index[pos]))
            if len(rows) >= limit:
                break
        return {"n_units": len(sel), "rows": rows[:limit]}


_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


def get_analysis(dataset_key: tuple, df: pd.DataFrame, spec: dict) -> Analysis:
    import json
    key = (dataset_key, json.dumps(spec, sort_keys=True, ensure_ascii=False))
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    a = Analysis(df, spec)
    with _cache_lock:
        _cache[key] = a
        while len(_cache) > 6:
            _cache.popitem(last=False)
    return a
