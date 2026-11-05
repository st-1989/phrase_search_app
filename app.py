import html
import inspect
import io
import json
import re
import sys
from collections import Counter
import unicodedata
from datetime import datetime
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st


st.set_page_config(page_title="Japanese Phrase Search", layout="wide")


CSS = r"""
<style>
:root {
  --text: inherit;
  --muted: #a0a0a0;
  --border: rgba(128, 128, 128, 0.25);
  --subtle: #8a8a8a;
}

.block-container {
  max-width: 1200px;
  padding-top: 2rem;
  padding-bottom: 4rem;
}

.search-meta {
  color: var(--subtle);
  font-size: 0.95rem;
  margin: 0.2rem 0 0.8rem 0;
}

.result-card {
  padding: 0.4rem 0.1rem;
  border-bottom: 1px solid var(--border);
}

.result-text {
  font-size: 1.05rem;
  line-height: 1.65;
  word-break: break-word;
}

.context {
  color: var(--muted);
}

.match {
  color: var(--text);
  font-weight: 700;
}

.ellipsis {
  color: var(--muted);
}

details.result-card > summary {
  list-style: none;
  cursor: pointer;
}

details.result-card > summary::-webkit-details-marker {
  display: none;
}

details.result-card > summary:hover .context {
  color: #808080;
}

.meta {
  color: var(--subtle);
  font-size: 0.88rem;
  margin-bottom: 0.15rem;
}

.meta b {
  color: var(--text);
  font-weight: 600;
}

.full-text {
  margin-top: 0.4rem;
  padding: 0.5rem 0.8rem;
  background: rgba(128, 128, 128, 0.08);
  border-left: 3px solid var(--border);
  font-size: 1rem;
  line-height: 1.7;
  word-break: break-word;
}
</style>
"""



APP_DIR = Path(__file__).parent
DATA_DIRS = [APP_DIR, APP_DIR / "data"]
UPLOAD_OPTION = "ファイルをアップロード…"
FILE_PATTERNS = ["*.csv", "*.xlsx", "*.xls"]

# 入力ファイルのフォーマット（この列名で読み込む。検索対象は TEXT_COLUMN のみ）
TEXT_COLUMN = "本文"
YEAR_COLUMN = "年(CDEX)"
EXPECTED_COLUMNS = [
    "発行体ID(業務)", "発行体コード(業務)", "証券コード(CDEX)", YEAR_COLUMN, "公表日(CDEX)",
    "発行体名(CDEX)", "発行体名(業務)", "業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)",
    "公表日(業務)", "発行体格付(業務)", "格付の方向性(業務)", "格付アクション(業務)", "タグ", TEXT_COLUMN,
]
# 複数選択で絞り込む列（サイドバー）。年は範囲スライダーで指定する。
MULTI_FILTER_COLUMNS = ["業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)", "格付アクション(業務)"]

# 巨大ファイル対策: 変換済みデータ（Parquet）の保存先
CACHE_DIR = APP_DIR / ".cache"
CACHE_VERSION = "v1"  # 列の扱いを変えたら上げる（古いキャッシュを使わない）


def list_local_files() -> list[Path]:
    paths = []
    for data_dir in DATA_DIRS:
        if data_dir.is_dir():
            for pattern in FILE_PATTERNS:
                paths.extend(sorted(data_dir.glob(pattern)))
    return paths


def finalize_table(df: pd.DataFrame) -> pd.DataFrame:
    """必要な列だけ残し、絞り込み列はカテゴリ型、年は整数にしてメモリと処理を軽くする。"""
    df = df[[c for c in EXPECTED_COLUMNS if c in df.columns]].copy()
    for col in MULTI_FILTER_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("category")
    if YEAR_COLUMN in df.columns:
        df[YEAR_COLUMN] = pd.to_numeric(df[YEAR_COLUMN], errors="coerce").astype("Int64")
    return df


def parse_table(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSV / Excel を読み込む。ID・コードの先頭ゼロを保つため全列を文字列で読む。"""
    wanted = set(EXPECTED_COLUMNS)
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(file_bytes), dtype=str, usecols=lambda c: c in wanted)
        except Exception as exc:
            raise RuntimeError(f"Excelを読み込めませんでした: {exc}")
    last_error = None
    for enc in ["utf-8-sig", "utf-8", "cp932", "shift_jis"]:
        for engine in ("pyarrow", "c"):  # pyarrow は C エンジンより数倍速い。失敗したら C に切替
            try:
                kwargs = {"usecols": lambda c: c in wanted} if engine == "c" else {}
                return pd.read_csv(io.BytesIO(file_bytes), encoding=enc, dtype=str, engine=engine, **kwargs)
            except Exception as exc:
                last_error = exc
    raise RuntimeError(f"CSVを読み込めませんでした: {last_error}")


# cache_resource: 巨大な DataFrame をコピー/シリアライズせずに再実行間で共有する（読み取り専用で扱うこと）
@st.cache_resource(show_spinner="読み込み中…（初回のみ時間がかかります）", max_entries=5)
def load_local_file(path: str, mtime_ns: int) -> pd.DataFrame:
    """初回は CSV/Excel を読んで Parquet に変換して保存し、2回目以降は Parquet から高速に読む。"""
    src = Path(path)
    stem = f"{src.stem}-{src.suffix[1:]}"
    cache_file = CACHE_DIR / f"{stem}-{mtime_ns}-{CACHE_VERSION}.parquet"
    if cache_file.exists():
        try:
            return pd.read_parquet(cache_file)
        except Exception:
            cache_file.unlink(missing_ok=True)
    df = finalize_table(parse_table(src.read_bytes(), src.name))
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        for old in CACHE_DIR.glob(f"{stem}-*.parquet"):
            old.unlink(missing_ok=True)
        df.to_parquet(cache_file)
    except Exception:
        pass  # キャッシュに書けなくても読み込み自体は成功させる
    return df


@st.cache_resource(show_spinner="読み込み中…", max_entries=3)
def load_uploaded(file_bytes: bytes, filename: str) -> pd.DataFrame:
    return finalize_table(parse_table(file_bytes, filename))


def normalize_text(text: str, use_nfkc: bool, ignore_spaces: bool) -> str:
    text = str(text)
    if use_nfkc:
        text = unicodedata.normalize("NFKC", text)
    if ignore_spaces:
        text = re.sub(r"\s+", "", text)
    return text


def mask_excluded(text: str, phrases) -> str:
    """除外フレーズに当たる部分を同じ長さの \\x00 に置き換える（位置はずらさない）。
    例: 「強固」を検索して「比較的強固」を除外すると、「比較的強固」の中の「強固」は一致しない。"""
    for ph in sorted(phrases, key=len, reverse=True):
        text = text.replace(ph, "\x00" * len(ph))
    return text


def find_all_literal(text: str, query: str) -> list[tuple[int, int]]:
    if not query:
        return []
    spans = []
    start = 0
    while True:
        idx = text.find(query, start)
        if idx < 0:
            break
        spans.append((idx, idx + len(query)))
        start = idx + max(1, len(query))
    return spans


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def build_snippet(text: str, spans: list[tuple[int, int]], context_chars: int = 45) -> str:
    if not spans:
        return html.escape(text)

    spans = merge_spans(spans)
    first_start = spans[0][0]
    last_end = spans[-1][1]

    window_start = max(0, first_start - context_chars)
    window_end = min(len(text), last_end + context_chars)

    visible_spans = []
    for start, end in spans:
        if end <= window_start or start >= window_end:
            continue
        visible_spans.append((max(start, window_start), min(end, window_end)))

    parts = []
    if window_start > 0:
        parts.append('<span class="ellipsis">…</span>')

    cursor = window_start
    for start, end in visible_spans:
        if start > cursor:
            parts.append(f'<span class="context">{html.escape(text[cursor:start])}</span>')
        parts.append(f'<span class="match">{html.escape(text[start:end])}</span>')
        cursor = end

    if cursor < window_end:
        parts.append(f'<span class="context">{html.escape(text[cursor:window_end])}</span>')

    if window_end < len(text):
        parts.append('<span class="ellipsis">…</span>')

    return "".join(parts)


def build_full_text(text: str, spans: list[tuple[int, int]]) -> str:
    parts = []
    cursor = 0
    for start, end in merge_spans(spans):
        if start > cursor:
            parts.append(f'<span class="context">{html.escape(text[cursor:start])}</span>')
        parts.append(f'<span class="match">{html.escape(text[start:end])}</span>')
        cursor = end
    if cursor < len(text):
        parts.append(f'<span class="context">{html.escape(text[cursor:])}</span>')
    return "".join(parts)


# ---- ユーザー別の検索履歴（JSONファイルに保存。.gitignore 済み） ----
HISTORY_PATH = APP_DIR / ".search_history.json"
HISTORY_LIMIT = 200   # 1ユーザーあたりの保存件数
RECENT_SHOWN = 10     # トップ画面の「最近の検索」に出す件数
DEFAULT_USER = "ゲスト"
NEW_USER_OPTION = "＋ 新しいユーザーを追加…"


def load_history() -> dict:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    data["users"].setdefault(DEFAULT_USER, [])
    return data


def save_history(data: dict) -> None:
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def _entry_key(e: dict) -> tuple:
    return (e["query"], e.get("query2", ""), e.get("mode", ""))


def _is_typing_step(a: tuple, b: tuple) -> bool:
    """a→b が、同じ欄の入力途中（語の延長・短縮）に当たるか。"""
    def ext(x: str, y: str) -> bool:
        return x.startswith(y) or y.startswith(x)
    return a[2] == b[2] and ((a[1] == b[1] and ext(a[0], b[0])) or (a[0] == b[0] and ext(a[1], b[1])))


def record_search(user: str, query: str, query2: str, mode: str, dataset: str, hits: int) -> None:
    """検索を履歴に記録する（語が2つのときは条件も保存）。即時検索では1文字ごとに呼ばれるため、
    直前の履歴の入力途中（語の延長・短縮）なら置き換え、同じ検索は先頭に寄せる。"""
    key = (query, query2, mode if query2 else "")
    data = load_history()
    entries = data["users"].setdefault(user, [])
    if entries and _entry_key(entries[0]) == key and entries[0]["dataset"] == dataset and entries[0]["hits"] == hits:
        return
    last = st.session_state.get("_last_recorded")
    if last and last[0] == user and entries and _entry_key(entries[0]) == last[1] and _is_typing_step(last[1], key):
        entries.pop(0)
    entries[:] = [e for e in entries if _entry_key(e) != key]
    entries.insert(0, {
        "query": query,
        "query2": query2,
        "mode": key[2],
        "dataset": dataset,
        "hits": hits,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    del entries[HISTORY_LIMIT:]
    save_history(data)
    st.session_state["_last_recorded"] = (user, key)


def clear_history(user: str) -> None:
    data = load_history()
    data["users"][user] = []
    save_history(data)
    st.session_state.pop("_last_recorded", None)


def add_user(name: str) -> None:
    """ユーザーを追加して選択状態にする（ボタンのコールバックなので widget の値を書き換えられる）。"""
    data = load_history()
    data["users"].setdefault(name, [])
    save_history(data)
    st.session_state["user_select"] = name
    st.session_state["new_user_name"] = ""


def set_query(query: str, query2: str = "", mode: str = "") -> None:
    st.session_state["query"] = query
    st.session_state["query2"] = query2
    if mode and mode in st.session_state.get("_mode_options", []):
        st.session_state["search_mode"] = mode


def meta_html(row: pd.Series) -> str:
    """結果1件分のメタ情報（発行体・年・公表日・格付など）を1行のHTMLにする。"""
    def v(col: str) -> str:
        val = row.get(col)
        return "" if pd.isna(val) else html.escape(str(val))

    rating = " ".join(x for x in [v("発行体格付(業務)"), v("格付の方向性(業務)")] if x)
    items = [
        ("", f"<b>{v('発行体名(CDEX)')}</b>"),
        ("年", v(YEAR_COLUMN)),
        ("公表日", v("公表日(CDEX)")),
        ("業種", v("業種(業務)")),
        ("格付", rating),
        ("アクション", v("格付アクション(業務)")),
        ("担当", v("担当アナリスト(CDEX)")),
        ("タグ", v("タグ")),
    ]
    return " · ".join(f"{label} {val}".strip() for label, val in items if val and val != "<b></b>")


MODE_AND_ROW = "両方を含む（同じ文）"
MODE_AND_REPORT = "両方を含む（同じレポート内）"
MODE_OR = "どちらかを含む"


def find_hits(df: pd.DataFrame, text_column: str, terms: list[str], mode: str,
              report_cols: list[str] | None, excl: tuple = ()) -> np.ndarray:
    """全行を一括（ベクトル化）で完全一致判定した真偽配列。語が2つのときは条件で組み合わせる。
    「同じレポート内」は、両方の語がレポート内のどこかにあれば、そのレポート内で語を含む行を一致とする。"""
    col = df[text_column]
    for ph in sorted(excl, key=len, reverse=True):  # 除外フレーズの部分は一致させない
        col = col.str.replace(ph, "\x00" * len(ph), regex=False)
    masks = [col.str.contains(t, regex=False, na=False).to_numpy() for t in terms]
    if len(masks) == 1:
        return masks[0]
    a, b = masks
    if mode == MODE_OR:
        return a | b
    if mode == MODE_AND_ROW or not report_cols:
        return a & b
    rid = df.groupby(report_cols, sort=False, observed=True, dropna=False).ngroup().to_numpy()
    n = int(rid.max()) + 1
    has_a, has_b = np.zeros(n, bool), np.zeros(n, bool)
    has_a[rid[a]] = True
    has_b[rid[b]] = True
    return (has_a & has_b)[rid] & (a | b)


def exact_search(
    df: pd.DataFrame,
    hit: np.ndarray,
    text_column: str,
    queries: list[str],
    max_results: int,
    context_chars: int,
    excl: tuple = (),
) -> list[dict]:
    """ヒットした上位 max_results 件だけ Python でスパン計算・HTML 化する（全ての検索語をハイライト）。"""
    subset = df[hit].head(max_results)
    results = []
    for pos in range(len(subset)):
        row = subset.iloc[pos]
        text = str(row[text_column])
        masked = mask_excluded(text, excl)
        spans = [sp for q in queries for sp in find_all_literal(masked, q)]
        if not spans:
            continue
        results.append(
            {
                "meta": meta_html(row),
                "text": text,
                "spans": spans,
                "snippet": build_snippet(text, spans, context_chars=context_chars),
                "count": len(spans),
            }
        )
    return results


# ---- 集計・共起語分析 ----
RATING_COLUMN = "発行体格付(業務)"
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
                "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"]
GROUP_COLUMNS = [RATING_COLUMN, "格付の方向性(業務)", "格付アクション(業務)", "業種(業務)", YEAR_COLUMN]
TOKENIZER_FAST = "高速（文字種で分割）"
TOKENIZER_JANOME = "形態素解析（Janome・名詞）"
RANK_BY_SCORE = "特徴度（一致文書で特に多い語）"
RANK_BY_COUNT = "出現文書数"

_RATING_SYMBOL = re.compile(r"(?:AAA|AA|A|BBB|BB|B|CCC|CC|C|D)[+-]?")
_RUN_PATTERN = re.compile(r"[一-龥々〆]{2,}|[ァ-ヴー]{2,}|[A-Za-z][A-Za-z&/\-]+")
try:
    from janome.tokenizer import Tokenizer as _JanomeTokenizer
except ImportError:
    _JanomeTokenizer = None


@st.cache_resource(show_spinner=False)
def _token_memo() -> dict:
    """文書ごとの語の集合を覚えておく（同じ文書を再解析しない）。"""
    return {}


@st.cache_resource(show_spinner=False)
def _janome():
    return _JanomeTokenizer()


def _tokens_fast(text: str) -> frozenset:
    return frozenset(_RUN_PATTERN.findall(text))


_WORD_CHARS = re.compile(r"[一-龥々ァ-ヴーぁ-んA-Za-z0-9]+")
_JANOME_NOUN = {"一般", "固有名詞", "サ変接続", "形容動詞語幹"}


def _tokens_janome(text: str) -> frozenset:
    out = set()
    for tok in _janome().tokenize(text):
        pos = tok.part_of_speech.split(",")
        if (
            pos[0] == "名詞" and pos[1] in _JANOME_NOUN and len(tok.surface) >= 2
            and not tok.surface.isdigit() and _WORD_CHARS.fullmatch(tok.surface)  # 記号混じりの語を除く
        ):
            out.add(tok.surface)
    return frozenset(out)


def doc_token_sets(texts, method: str, remove: tuple = (), mask: tuple = ()) -> list[frozenset]:
    """文ごとの語の集合。remove の語（検索フレーズ）は先に取り除く。
    これで「比較的高水準」から「比較的」で検索したとき、「高水準」が共起語として取れる。"""
    memo = _token_memo()
    fn = _tokens_janome if method == TOKENIZER_JANOME else _tokens_fast
    out = []
    for text in texts:
        key = (method, hash(text), remove, mask)
        if key not in memo:
            t = str(text)
            for ph in mask:  # 除外フレーズは語の切り出しからも外す
                t = t.replace(ph, " ")
            for q in remove:
                t = t.replace(q, " ")
            memo[key] = fn(t)
        out.append(memo[key])
    return out


def sample_positions(positions: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    if len(positions) <= n:
        return positions
    return np.sort(np.random.default_rng(seed).choice(positions, n, replace=False))


def enriched_terms(fg, bg, queries, exclude, min_count, top_n, rank_by) -> pd.DataFrame:
    """fg（注目する文書群）に多い語を、bg（比較対象）との 2x2 対数尤度比 G² で評価する。"""
    n_fg, n_bg = len(fg), len(bg)
    fg_count, bg_count = Counter(), Counter()
    for t in fg:
        fg_count.update(t)
    for t in bg:
        bg_count.update(t)
    terms = [
        w for w, c in fg_count.items()
        if c >= min_count and w not in exclude and all(q not in w and w not in q for q in queries)
        and not _RATING_SYMBOL.fullmatch(w)  # 格付記号（AA, BBB- など）は語として扱わない
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

    g2 = 2 * (
        xlogx(a, n_fg * col1 / total) + xlogx(b, n_fg * col2 / total)
        + xlogx(c, n_bg * col1 / total) + xlogx(d, n_bg * col2 / total)
    )
    rate_fg, rate_bg = a / n_fg, c / n_bg
    lift = rate_fg / ((c + 0.5) / (n_bg + 1))
    table = pd.DataFrame({
        "語": terms, "出現文書数": a.astype(int), "出現率": rate_fg,
        "比較対象の出現率": rate_bg, "リフト": lift, "特徴度": g2,
    })
    if rank_by == RANK_BY_SCORE:
        table = table[rate_fg > rate_bg].sort_values("特徴度", ascending=False)
    else:
        table = table.sort_values(["出現文書数", "特徴度"], ascending=False)
    return table.head(top_n).reset_index(drop=True)


def group_order(values, column: str) -> list:
    values = [v for v in values if pd.notna(v)]
    if column == RATING_COLUMN:
        return sorted(values, key=lambda v: (RATING_ORDER.index(v) if v in RATING_ORDER else 99, str(v)))
    return sorted(values)


TERM_COLUMN_CONFIG = {
    "出現率": st.column_config.NumberColumn(format="percent"),
    "比較対象の出現率": st.column_config.NumberColumn(format="percent"),
    "リフト": st.column_config.NumberColumn(format="%.1f"),
    "特徴度": st.column_config.NumberColumn("特徴度(G²)", format="%.1f"),
}


def combo_key(values: tuple, columns: list[str]) -> tuple:
    """複数軸の組み合わせを、各軸の自然な順（格付は AAA→…）で並べるためのキー。"""
    return tuple(
        (RATING_ORDER.index(v) if v in RATING_ORDER else 99, str(v)) if c == RATING_COLUMN else v
        for v, c in zip(values, columns)
    )


def combo_label(values: tuple) -> str:
    return " / ".join(str(v) for v in values)


def wilson_interval(k, n, z: float = 1.96):
    """一致率の Wilson 信頼区間（既定は 95%）。件数が少ない区分の不確かさを示す。"""
    k, n = np.asarray(k, float), np.asarray(n, float)
    with np.errstate(all="ignore"):
        p = k / n
        denom = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return center - half, center + half


def aggregate_by(df: pd.DataFrame, hit: np.ndarray, columns: list[str]) -> tuple[pd.DataFrame, float]:
    """軸（1つ以上）ごとの一致件数・全文書数・一致率・信頼区間・全体比・標準化残差。"""
    total = df.groupby(columns, observed=True).size().rename("全文書数")
    hits = df[hit].groupby(columns, observed=True).size().rename("一致件数")
    t = pd.concat([total, hits], axis=1).fillna(0).astype(int).reset_index()
    p0 = float(hit.sum()) / len(df)  # 全体の一致率（比較の基準）
    n, k = t["全文書数"].to_numpy(float), t["一致件数"].to_numpy(float)
    t["一致率"] = k / n
    t["下限"], t["上限"] = wilson_interval(k, n)
    t["全体比"] = t["一致率"] / p0
    t["期待件数"] = n * p0
    with np.errstate(all="ignore"):
        t["標準化残差"] = (k - n * p0) / np.sqrt(n * p0 * (1 - p0))
    return t, p0


COUNT_SORTS = ["軸の順", "一致率（高い順）", "全体比（高い順）", "標準化残差（絶対値の大きい順）", "一致件数（多い順）"]
CHART_METRICS = {  # 指標: (表示形式, 中心値。None なら一方向の色)。先頭が既定
    "一致件数": ("{:,.0f}", None),
    "一致率": ("{:.0%}", None),
    "全文書数": ("{:,.0f}", None),
    "全体比": ("{:.1f}", 1.0),
    "標準化残差": ("{:.1f}", 0.0),
}


def text_px(text: str, size: float) -> float:
    """ラベルの概算幅（全角は文字サイズ分、半角は約0.55倍）。"""
    return sum(size if ord(ch) > 255 else size * 0.55 for ch in text)


def count_view(df: pd.DataFrame, hit: np.ndarray, group_cols: list[str], unit_name: str = "文") -> None:
    table, p0 = aggregate_by(df, hit, group_cols)
    c1, c2, c3 = st.columns(3)
    chart_metric = c1.selectbox("グラフの指標", list(CHART_METRICS), key="chart_metric",
                                help=f"一致件数・全文書数は元の件数（単位: {unit_name}）、一致率は全文書数に占める割合です。")
    sort_by = c2.selectbox("並び順", COUNT_SORTS, key="count_sort")
    min_docs = c3.number_input("最小の全文書数", 1, 100000, 1, key="count_min_docs",
                               help="これより文書数が少ない組み合わせは表示しません（率が不安定なため）。")
    table = table[table["全文書数"] >= min_docs]
    if table.empty:
        st.info("条件に合う組み合わせがありません。")
        return

    keys = [combo_key(tuple(r), group_cols) for r in table[group_cols].itertuples(index=False)]
    table = table.assign(_k=keys).sort_values("_k")  # まず軸の順
    if sort_by.startswith("一致率"):
        table = table.sort_values("一致率", ascending=False, kind="stable")
    elif sort_by.startswith("全体比"):
        table = table.sort_values("全体比", ascending=False, kind="stable")
    elif sort_by.startswith("標準化残差"):
        table = table.iloc[np.argsort(-table["標準化残差"].abs().fillna(0).to_numpy(), kind="stable")]
    elif sort_by.startswith("一致件数"):
        table = table.sort_values("一致件数", ascending=False, kind="stable")
    table = table.drop(columns="_k").reset_index(drop=True)

    label = table[group_cols].apply(lambda r: combo_label(tuple(r)), axis=1)
    size = ui["size"]
    # ラベルを間引かず全部表示する: 軸ラベルの重なり除去と省略を止め、カテゴリ数×ラベル幅に応じて図の幅を広げる
    axis_font = dict(labelFontSize=size, titleFontSize=size, labelOverlap=False, labelLimit=0)
    config = dict(font=ui["family"] or "sans-serif")
    fmt, mid = CHART_METRICS[chart_metric]
    col = chart_metric

    if len(group_cols) == 1:
        d = pd.DataFrame({"label": label, "val": table[col], "rate": table["一致率"], "lo": table["下限"],
                          "hi": table["上限"], "n": table["全文書数"], "hits": table["一致件数"]})
        order = d["label"].tolist()
        step = int(max(44, max(text_px(l, size) for l in order) + 18))
        axis_fmt = alt.Axis(format="%") if col == "一致率" else alt.Axis()
        bars = alt.Chart(d).mark_bar().encode(
            x=alt.X("label:N", sort=order, title=group_cols[0], axis=alt.Axis(labelAngle=0)),
            y=alt.Y("val:Q", title=col, axis=axis_fmt),
            tooltip=["label", "hits", "n", alt.Tooltip("rate:Q", format=".1%")],
        )
        layers = [bars]
        if col == "一致率":  # 信頼区間（黒線）と全体の一致率（点線）
            layers.append(alt.Chart(d).mark_rule(color="black", strokeWidth=2).encode(
                x=alt.X("label:N", sort=order), y="lo:Q", y2="hi:Q"))
            refs = [p0]
        elif col == "全体比":
            refs = [1.0]
        elif col == "標準化残差":
            refs = [-2.0, 0.0, 2.0]
        else:
            refs = []
        for y in refs:
            layers.append(alt.Chart(pd.DataFrame({"y": [y]})).mark_rule(color="gray", strokeDash=[6, 4]).encode(y="y:Q"))
        chart = alt.layer(*layers).properties(width=alt.Step(step), height=320).configure(**config).configure_axis(**axis_font)
        st.altair_chart(chart, use_container_width=False)
    elif len(group_cols) == 2:
        yc, xc = group_cols
        vals = table[col]
        center = mid if mid is not None else float(vals.mean())
        dev = float((vals - center).abs().max()) or 1.0
        txt = vals.map(lambda v: "" if pd.isna(v) else fmt.format(v))
        d = pd.DataFrame({
            "y": table[yc].astype(str), "x": table[xc].astype(str), "val": vals, "txt": txt,
            "tc": np.where((vals - center).abs() > dev / 2, "white", "black"),  # 濃いセルは白文字
            "n": table["全文書数"], "hits": table["一致件数"],
        })
        xo = [str(v) for v in group_order(table[xc].unique(), xc)]
        yo = [str(v) for v in group_order(table[yc].unique(), yc)]
        scale = (alt.Scale(scheme="redblue", domainMid=mid, reverse=True) if mid is not None
                 else alt.Scale(scheme="blues"))
        enc = dict(x=alt.X("x:N", sort=xo, title=xc, axis=alt.Axis(labelAngle=0)),
                   y=alt.Y("y:N", sort=yo, title=yc))
        rect = alt.Chart(d).mark_rect().encode(
            color=alt.Color("val:Q", title=col, scale=scale), tooltip=["y", "x", "hits", "n", "txt"], **enc)
        text = alt.Chart(d).mark_text(fontSize=size * 0.8).encode(
            text="txt:N",
            color=alt.Color("tc:N", legend=None, scale=alt.Scale(domain=["black", "white"], range=["black", "white"])),
            **enc)
        cell_w = int(max(text_px(x, size) for x in xo + ["-00.0"]) + 18)
        cell_h = int(size * 2.2)
        chart = (rect + text).properties(width=alt.Step(cell_w), height=alt.Step(cell_h)) \
            .configure(**config).configure_axis(**axis_font)
        st.altair_chart(chart, use_container_width=False)
    else:
        st.caption("3軸以上は表のみで表示します（グラフは 1〜2 軸のとき）。")

    st.dataframe(
        table, hide_index=True, width="stretch",
        column_config={
            "一致率": st.column_config.NumberColumn(format="percent"),
            "下限": st.column_config.NumberColumn("信頼区間 下限", format="percent"),
            "上限": st.column_config.NumberColumn("信頼区間 上限", format="percent"),
            "全体比": st.column_config.NumberColumn(format="%.2f"),
            "期待件数": st.column_config.NumberColumn(format="%.1f"),
            "標準化残差": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.caption(
        f"全体の一致率は {p0:.1%}（{int(hit.sum()):,} / {len(df):,}）。"
        "一致率の区間は Wilson の 95% 信頼区間です（件数が少ないほど広くなります）。"
        "全体比 = 一致率 ÷ 全体の一致率。期待件数 = 全文書数 × 全体の一致率。"
        "標準化残差 = (一致件数 − 期待件数) ÷ √(全文書数 × 全体率 × (1 − 全体率))で、絶対値が 2 を超えると偶然とは考えにくい偏りの目安です。"
        "グラフの点線は全体の一致率、黒線は信頼区間です。"
    )


UNIT_ROW = "文（1行を1件）"
UNIT_REPORT = "レポート（発行体ID＋公表日で1件）"


def report_key_columns(df: pd.DataFrame) -> list[str] | None:
    """レポートを1件に定める列（発行体ID＋公表日）。公表日(業務)が無ければ公表日(CDEX)で代用する。"""
    if "発行体ID(業務)" not in df.columns:
        return None
    for date_col in ("公表日(業務)", "公表日(CDEX)"):
        if date_col in df.columns:
            return ["発行体ID(業務)", date_col]
    return None


class Units:
    """集計・分析の単位。行を1件とするか、レポート（同じ発行体ID・公表日の行の集まり）を1件とするかを同じ形で扱う。

    n: 単位の数 / hit: 単位ごとの一致（レポートは「1行でも一致すれば一致」）
    first_idx: 単位の先頭行（軸の値はここから取る） / order, starts, counts: 単位に属する行の位置
    """

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

    def frame(self, df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        """単位ごとの軸の値（単位の先頭行の値。レポート内で値は同じ前提）。"""
        return df[columns].iloc[self.first_idx].reset_index(drop=True)

    def token_sets(self, texts: pd.Series, units: np.ndarray, method: str,
                   only_hit: bool = False, remove: tuple = (), mask: tuple = ()) -> list[frozenset]:
        """指定した単位の語の集合（レポートは所属する行の語の和集合）。
        only_hit=True なら、検索語を含む文（行）の語だけを数える＝同じ文の中での共起に限る。"""
        pos = [self.order[self.starts[u]: self.starts[u] + self.counts[u]] for u in units]
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


def term_context_view(df: pd.DataFrame, units: Units, unit_ids: np.ndarray, token_sets: list[frozenset],
                      term: str, queries: list[str], unit_name: str, excl: tuple = ()) -> None:
    """選んだ語が実際にどんな文で使われているかを、検索フレーズと語の両方をハイライトして表示する。
    共起は1つの文の中に限る（検索フレーズと語が同じ文にある文だけを出す）。"""
    sel = [int(u) for u, t in zip(unit_ids, token_sets) if term in t]
    st.markdown(f"#### 「{term}」が使われている文")
    if not sel:
        st.info("該当する文がありません。")
        return
    keys = list(queries) + [term]
    texts = df[text_column]
    cards, shown = [], 0
    for u in sel:
        rows = units.order[units.starts[u]: units.starts[u] + units.counts[u]]
        for pos in sorted(rows):
            text = str(texts.iloc[pos])
            masked = mask_excluded(text, excl)
            spans = [sp for k in keys for sp in find_all_literal(masked, k)]
            if not spans or term not in masked or not any(q in masked for q in queries):
                continue  # 検索フレーズと語が同じ文にあるものだけ
            if shown >= max_results:
                break
            shown += 1
            cards.append(
                f"""
                <details class="result-card">
                  <summary>
                    <div class="meta">{meta_html(df.iloc[pos])}</div>
                    <div class="result-text">{build_snippet(text, spans, context_chars=context_chars)}</div>
                  </summary>
                  <div class="full-text">{build_full_text(text, spans)}</div>
                </details>
                """
            )
        if shown >= max_results:
            break
    st.caption(f"この語を含む{unit_name} {len(sel):,} 件のうち、{shown:,} 件の文を表示（文章をクリックで全文表示）。")
    st.html("".join(cards))


def analysis_view(view: str, df: pd.DataFrame, hit: np.ndarray, queries: list[str], excl: tuple = ()) -> None:
    texts = df[text_column]

    group_cols = [RATING_COLUMN]
    if view in (VIEW_COUNT, VIEW_GROUP_TERMS):
        options = [c for c in GROUP_COLUMNS if c in df.columns]
        if not options:
            st.info("集計に使える列がありません。")
            return
        group_cols = st.multiselect(
            "集計の軸（複数選択可。選んだ順に左→右の軸になります）", options,
            default=[c for c in [RATING_COLUMN] if c in options], key="group_cols",
        )
        if not group_cols:
            st.info("集計の軸を1つ以上選んでください。")
            return

    # 集計の単位: 1文1行のデータでは、レポートの長さ（文数）の違いが件数・一致率に影響するため、レポート単位も選べる
    report_cols = report_key_columns(df)
    unit_options = [UNIT_ROW] + ([UNIT_REPORT] if report_cols else [])
    unit = st.radio(
        "集計の単位", unit_options, index=len(unit_options) - 1, horizontal=True, key="unit",
        help="文: 1行ごとに数えます。レポート: 発行体IDと公表日が同じ行を1件にまとめ、1行でも一致すれば一致とします"
             "（1文1行のデータでは、文数の多いレポートが有利になる影響を除けます）。共起語も同じ単位で数えます。",
    )
    if not report_cols:
        st.caption("発行体ID(業務)・公表日の列が無いため、レポート単位は選べません。")
    units = Units(df, hit, report_cols if unit == UNIT_REPORT else None)
    unit_name = "レポート" if units.is_report else "文"
    st.caption(f"単位: {unit_name}。一致 {int(units.hit.sum()):,} / 全 {units.n:,} {unit_name}"
               + (f"（{report_cols[0]} と {report_cols[1]} で区別）" if units.is_report else ""))

    if view == VIEW_COUNT:
        count_df = units.frame(df, group_cols) if units.is_report else df
        count_view(count_df, units.hit, group_cols, unit_name)
        return

    with st.expander("分析の設定", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        methods = [TOKENIZER_FAST] + ([TOKENIZER_JANOME] if _JanomeTokenizer else [])
        method = c1.selectbox("語の切り出し", methods, key="tok_method",
                              help="Janome は精度が高い反面、遅いです。初回の解析結果は記憶されます。")
        rank_by = c2.selectbox("並び順", [RANK_BY_COUNT, RANK_BY_SCORE], key="rank_by")
        all_terms = c3.checkbox("語を全て表示", value=True, key="all_terms")
        top_n = sys.maxsize if all_terms else c3.slider("表示する語数", 10, 500, 30, 5, key="top_n")
        min_count = c4.slider("最小出現数", 1, 20, 3, key="min_count",
                              help="その語を含む（文／レポート）の数の下限です。")
        all_docs = c1.checkbox("全件を分析に使う", value=True, key="all_docs",
                               help="オフにすると、一致・比較対象それぞれ、指定した件数まで無作為に抽出して解析します（速くなります）。")
        max_docs = sys.maxsize if all_docs else c1.slider("分析に使う最大件数", 100, 3000, 500, 100, key="max_docs")
        exclude = set(c2.text_input("除外する語（スペース区切り）", key="exclude").split())
    if _JanomeTokenizer is None:
        st.caption("Janome が未インストールのため高速モードのみ利用できます（`pip install janome` で形態素解析が使えます）。")

    remove = tuple(sorted(queries, key=len, reverse=True))  # 検索フレーズは取り除いて語を切り出す
    hit_units = np.flatnonzero(units.hit)
    fg_units = sample_positions(hit_units, max_docs)

    if view == VIEW_TERMS:
        with st.spinner("共起語を解析中…"):
            fg = units.token_sets(texts, fg_units, method, only_hit=True, remove=remove, mask=excl)
            bg = units.token_sets(texts, sample_positions(np.arange(units.n), max_docs, seed=1), method, remove=remove, mask=excl)
            table = enriched_terms(fg, bg, queries, exclude, min_count, top_n, rank_by)
        st.caption(f"一致した{unit_name} {len(fg_units):,} 件（全 {len(hit_units):,} 件から抽出）を、"
                   f"全{unit_name}の無作為標本 {len(bg):,} 件と比較しています。")
        if table.empty:
            st.info("条件に合う語がありませんでした。最小出現数を下げてみてください。")
            return
        event = st.dataframe(
            table, hide_index=True, width="stretch", column_config=TERM_COLUMN_CONFIG,
            on_select="rerun", selection_mode="single-row", key="term_table",
        )
        st.caption("語の行をクリックすると、その語が実際に使われている文を表示します。")
        if event.selection.rows:
            term = table["語"].iloc[event.selection.rows[0]]
            term_context_view(df, units, fg_units, fg, term, queries, unit_name, excl)
        return

    # VIEW_GROUP_TERMS: 軸の値ごとに、一致した単位の中で特徴的な語（比較対象は一致した単位全体）
    with st.spinner("軸ごとの共起語を解析中…"):
        all_fg = units.token_sets(texts, fg_units, method, only_hit=True, remove=remove, mask=excl)
        gdf = df[group_cols].iloc[units.first_idx[fg_units]]
        valid = gdf.notna().all(axis=1).to_numpy()
        combos = np.empty(len(fg_units), dtype=object)
        combos[:] = list(zip(*[gdf[c].to_numpy() for c in group_cols]))
        columns, skipped, raw_terms, members = {}, [], {}, {}
        for value in sorted(set(combos[valid]), key=lambda v: combo_key(v, group_cols)):
            idx = np.flatnonzero(valid & np.array([c == value for c in combos]))
            name = combo_label(value)
            if len(idx) < max(min_count, 3):
                skipped.append(f"{name}（{len(idx)}件）")
                continue
            fg = [all_fg[i] for i in idx]
            table = enriched_terms(fg, all_fg, queries, exclude, min_count, top_n, rank_by)
            columns[f"{name}（{len(idx)}件）"] = [
                f"{r.語} ({r.出現文書数})" for r in table.itertuples()
            ] if not table.empty else []
            raw_terms[f"{name}（{len(idx)}件）"] = list(table["語"]) if not table.empty else []
            members[f"{name}（{len(idx)}件）"] = idx
    axes_name = " × ".join(group_cols)
    st.caption(
        f"{axes_name}ごとに、一致した{unit_name}（{len(fg_units):,} 件）の中でその区分に特に多い語を表示します。語の後ろの数字は出現数です。"
        if rank_by == RANK_BY_SCORE else
        f"{axes_name}ごとに、一致した{unit_name}の中で出現数が多い語を表示します。語の後ろの数字は出現数です。"
    )
    if not columns:
        st.info("件数が足りず表示できませんでした。最小出現数を下げるか、絞り込みを緩めてください。")
        return
    height = max(len(v) for v in columns.values())
    st.dataframe(
        pd.DataFrame({k: v + [""] * (height - len(v)) for k, v in columns.items()}, index=range(1, height + 1)),
        width="stretch",
    )
    if skipped:
        st.caption("件数が少ないため省略: " + "、".join(skipped))

    st.markdown("**語の使われ方を見る**")
    s1, s2 = st.columns(2)
    col_name = s1.selectbox("区分", list(columns), key="ctx_group")
    cand = raw_terms.get(col_name, [])
    if cand:
        term = s2.selectbox("語", cand, key="ctx_term")
        idx = members[col_name]
        term_context_view(df, units, fg_units[idx], [all_fg[i] for i in idx], term, queries, unit_name, excl)


VIEW_RESULTS = "検索結果"
VIEW_COUNT = "件数の集計"
VIEW_TERMS = "共起語"
VIEW_GROUP_TERMS = "区分別の共起語"


# ---- 表示設定（フォント・文字サイズ。選択は .search_history.json に保存され次回も維持される） ----
CUSTOM_FONT_LABEL = "その他（フォント名を入力）"
DEFAULT_FONT_LABEL = "BIZ UDPゴシック"
DEFAULT_FONT_SIZE = 18  # px。Streamlit 標準は 16px
# 表示名: (CSSのfont-family名, Google Fontsから読み込むか, 代替の総称ファミリ)
FONT_CHOICES = {
    DEFAULT_FONT_LABEL: ("BIZ UDPGothic", True, "sans-serif"),
    "BIZ UDゴシック": ("BIZ UDGothic", True, "sans-serif"),
    "BIZ UDP明朝": ("BIZ UDPMincho", True, "serif"),
    "Noto Sans JP": ("Noto Sans JP", True, "sans-serif"),
    "Noto Serif JP": ("Noto Serif JP", True, "serif"),
    "Zen Kaku Gothic New": ("Zen Kaku Gothic New", True, "sans-serif"),
    "IBM Plex Sans JP": ("IBM Plex Sans JP", True, "sans-serif"),
    "ヒラギノ角ゴ（Mac）": ("Hiragino Sans", False, "sans-serif"),
    "游ゴシック": ("Yu Gothic", False, "sans-serif"),
    "メイリオ": ("Meiryo", False, "sans-serif"),
    CUSTOM_FONT_LABEL: ("", True, "sans-serif"),
}


def current_ui() -> dict:
    """保存済みの設定を初期値にして、現在のフォント・文字サイズを返す。"""
    saved = load_history().get("ui", {})
    st.session_state.setdefault("ui_font", saved.get("font", DEFAULT_FONT_LABEL))
    if st.session_state["ui_font"] not in FONT_CHOICES:
        st.session_state["ui_font"] = DEFAULT_FONT_LABEL
    st.session_state.setdefault("ui_font_custom", saved.get("font_custom", ""))
    st.session_state.setdefault("ui_font_size", int(saved.get("font_size", DEFAULT_FONT_SIZE)))

    family, web, generic = FONT_CHOICES[st.session_state["ui_font"]]
    if st.session_state["ui_font"] == CUSTOM_FONT_LABEL:
        family = re.sub(r"[\"';{}<>]", "", st.session_state["ui_font_custom"]).strip()
    names = [family] if family else []
    names += [n for n in ["BIZ UDPGothic", "Hiragino Sans", "Yu Gothic"] if n not in names]
    stack = ", ".join([f'"{n}"' for n in names] + ["Meiryo", generic])
    return {"family": family, "web": web, "stack": stack, "size": st.session_state["ui_font_size"]}


def save_ui_settings() -> None:
    data = load_history()
    data["ui"] = {
        "font": st.session_state["ui_font"],
        "font_custom": st.session_state["ui_font_custom"],
        "font_size": st.session_state["ui_font_size"],
    }
    save_history(data)


def font_css(ui: dict) -> str:
    """選んだフォントを Google Fonts から読み込み（オフラインや未提供ならシステムフォントに代替）、全体に適用する。"""
    imports = ""
    if ui["family"] and ui["web"]:
        spec = ui["family"].replace(" ", "+")
        weights = ":wght@400;700" if ui["family"] in {f for f, w, _ in FONT_CHOICES.values() if w} else ""
        imports = f"@import url('https://fonts.googleapis.com/css2?family={spec}{weights}&display=swap');"
    return f"""
<style>
{imports}
html {{ font-size: {ui['size']}px !important; }}
html, body, .stApp,
.stApp :is(p, span, div, label, li, a, h1, h2, h3, h4, h5, h6, input, textarea, button, th, td, summary, caption, small, strong, code):not([data-testid="stIconMaterial"]):not([class*="material-symbols"]) {{
  font-family: {ui['stack']} !important;
}}
</style>
"""


ui = current_ui()
st.markdown(font_css(ui) + CSS, unsafe_allow_html=True)

st.title("Japanese Phrase Search")
st.caption(f"CSV / Excel の「{TEXT_COLUMN}」列から、入力した日本語フレーズの完全一致をリアルタイム検索します。")

local_files = list_local_files()
dataset_labels = {str(path.relative_to(APP_DIR)): path for path in local_files}
dataset = st.selectbox("データセット", list(dataset_labels) + [UPLOAD_OPTION])

try:
    if dataset == UPLOAD_OPTION:
        uploaded = st.file_uploader("CSV / Excelファイル", type=["csv", "xlsx", "xls"])
        if uploaded is None:
            st.info("CSV または Excel ファイルをアップロードしてください。")
            st.stop()
        df = load_uploaded(uploaded.getvalue(), uploaded.name)
    else:
        path = dataset_labels[dataset]
        df = load_local_file(str(path), path.stat().st_mtime_ns)
except Exception as exc:
    st.error(str(exc))
    st.stop()

if TEXT_COLUMN not in df.columns:
    st.error(
        f"「{TEXT_COLUMN}」列が見つかりません。次の列を持つファイルを指定してください: "
        + ", ".join(EXPECTED_COLUMNS)
    )
    st.stop()

missing = [c for c in dict.fromkeys(EXPECTED_COLUMNS) if c not in df.columns]
if missing:
    st.warning("次の列がファイルにありません（該当する表示・絞り込みは省略されます）: " + ", ".join(missing))

if df.empty:
    st.warning("ファイルにデータがありません。")
    st.stop()

with st.sidebar:
    st.header("ユーザー")
    history = load_history()
    user_names = list(history["users"])
    chosen_user = st.selectbox("ユーザーを選択", user_names + [NEW_USER_OPTION], key="user_select")
    if chosen_user == NEW_USER_OPTION:
        new_name = st.text_input("新しいユーザー名", key="new_user_name").strip()
        st.button("追加", disabled=not new_name, on_click=add_user, args=(new_name,))
        user = None
    else:
        user = chosen_user

    st.header("表示")
    st.selectbox("フォント", list(FONT_CHOICES), key="ui_font", on_change=save_ui_settings)
    st.text_input(
        "フォント名", key="ui_font_custom", on_change=save_ui_settings,
        disabled=st.session_state["ui_font"] != CUSTOM_FONT_LABEL,
        help="Google Fonts にあるフォント名、または手元にインストール済みのフォント名を入力します。",
    )
    st.slider("文字サイズ [px]", 12, 32, key="ui_font_size", on_change=save_ui_settings)

    st.header("設定")
    context_chars = st.slider("前後の表示文字数", 10, 120, 45, 5)
    show_all = st.checkbox("全件表示", value=True, help="オフにすると、表示件数の上限を指定できます（件数が多いと重くなります）。")
    max_results = sys.maxsize if show_all else st.slider("最大表示件数", 10, 2000, 100, 10)

    st.subheader("絞り込み")
    mask = None  # 絞り込みが無いときは df をコピーしない

    if YEAR_COLUMN in df.columns and df[YEAR_COLUMN].notna().any():
        year_min, year_max = int(df[YEAR_COLUMN].min()), int(df[YEAR_COLUMN].max())
        if year_min < year_max:
            year_range = st.slider(YEAR_COLUMN, year_min, year_max, (year_min, year_max))
            if year_range != (year_min, year_max):
                mask = df[YEAR_COLUMN].between(*year_range).fillna(False).to_numpy()
        else:
            st.caption(f"{YEAR_COLUMN}: {year_min} のみ")

    for col in [RATING_COLUMN] + MULTI_FILTER_COLUMNS:
        if col not in df.columns:
            continue
        options = group_order(df[col].dropna().unique(), col)  # 格付符号は AAA→D の順
        chosen = st.multiselect(col, options, placeholder="すべて（複数選択可）")
        if chosen:
            m = df[col].isin(chosen).to_numpy()
            mask = m if mask is None else (mask & m)

    if mask is not None:
        df = df[mask]
        st.caption(f"絞り込み後: {len(df):,} 行")

text_column = TEXT_COLUMN


def recent_searches(user: str | None) -> None:
    """トップ画面（検索語が空のとき）の下に出す「最近の検索」。クリックで検索語を再入力する。"""
    st.divider()
    st.subheader("最近の検索")
    if user is None:
        st.caption("ユーザーを選択すると、検索履歴が表示されます。")
        return
    entries = load_history()["users"].get(user, [])
    if not entries:
        st.caption(f"{user} さんの検索履歴はまだありません。")
        return

    def row(i: int, e: dict) -> None:
        c1, c2 = st.columns([3, 5], vertical_alignment="center")
        q2, mode = e.get("query2", ""), e.get("mode", "")
        label = f'{e["query"]} ＋ {q2}' if q2 else e["query"]
        c1.button(label, key=f"recent_{i}", on_click=set_query, args=(e["query"], q2, mode), use_container_width=True)
        c2.caption(f'{e["at"]} · {e["hits"]:,} 件 · {e["dataset"]}' + (f" · {mode}" if q2 and mode else ""))

    for i, e in enumerate(entries[:RECENT_SHOWN]):
        row(i, e)
    if len(entries) > RECENT_SHOWN:
        with st.expander(f"さらに過去の履歴（{len(entries) - RECENT_SHOWN} 件）"):
            for i, e in enumerate(entries[RECENT_SHOWN:], start=RECENT_SHOWN):
                row(i, e)
    st.button("履歴を消去", key="clear_history", on_click=clear_history, args=(user,))


@st.fragment
def search_section(df: pd.DataFrame, dataset: str, user: str | None) -> None:
    # live=True は新しい Streamlit のみ対応（入力中に即検索）。古い版では Enter で検索。
    live_kwargs = {"live": True} if "live" in inspect.signature(st.text_input).parameters else {}
    report_cols = report_key_columns(df)
    mode_options = [MODE_AND_ROW] + ([MODE_AND_REPORT] if report_cols else []) + [MODE_OR]
    st.session_state["_mode_options"] = mode_options
    if st.session_state.get("search_mode") not in mode_options:  # 既定: レポート単位が使えるならそれ
        st.session_state["search_mode"] = mode_options[1] if report_cols else mode_options[0]

    c1, c2, c3 = st.columns([3, 3, 2], vertical_alignment="bottom")
    query = c1.text_input(
        "検索フレーズ",
        placeholder="例: 比較的強い",
        help="入力中の文字列を、そのまま完全一致で検索します。",
        key="query",
        **live_kwargs,
    )
    query2 = c2.text_input(
        "検索フレーズ2（任意）",
        placeholder="2つ目の語（空欄なら1語のみ）",
        help="2つ目の語を入れると、右の条件で組み合わせて検索します。",
        key="query2",
        **live_kwargs,
    )
    mode = c3.selectbox(
        "2語の条件", mode_options, key="search_mode", disabled=not (query and query2),
        help="同じ文: 1つの文に両方が入る / 同じレポート内: 別の文でも、同じレポートに両方が入る。",
    )

    # ユーザー切替直後の再実行では、前のユーザーの検索語を記録しない
    user_unchanged = st.session_state.get("_seen_user") == user
    st.session_state["_seen_user"] = user

    excl = tuple(st.text_input(
        "除外フレーズ（任意・スペース区切り）", placeholder="例: 比較的強固（「強固」で検索したとき、「比較的強固」の中の「強固」を除く）",
        help="ここに入れた語に含まれる部分は、検索の一致として数えません。1つの文に除外フレーズと単独の一致が両方あれば、単独の方は一致します。",
        key="exclude_phrase",
    ).split())
    terms = [t for t in (query, query2) if t]  # どちらか一方だけの入力でも、その1語で検索する
    if not terms:
        st.write(f"**{len(df):,} 行**を読み込みました。検索語を入力してください。")
        recent_searches(user)
        return

    hit = find_hits(df, text_column, terms, mode, report_cols, excl)
    total = int(hit.sum())
    scope = f"「{terms[0]}」と「{terms[1]}」{mode} · " if len(terms) == 2 else ""

    if user and user_unchanged:
        record_search(user, terms[0], terms[1] if len(terms) == 2 else "", mode, dataset, total)

    if total == 0:
        st.markdown(f'<div class="search-meta">{html.escape(scope)}完全一致: 全 0 件</div>', unsafe_allow_html=True)
        st.info("完全一致する句は見つかりませんでした。")
        return

    view = st.radio(
        "表示", [VIEW_RESULTS, VIEW_COUNT, VIEW_TERMS, VIEW_GROUP_TERMS],
        horizontal=True, key="view", label_visibility="collapsed",
    )
    if view != VIEW_RESULTS:
        st.markdown(
            f'<div class="search-meta">{html.escape(scope)}完全一致: 全 {total:,} 件 / {len(df):,} 行</div>',
            unsafe_allow_html=True,
        )
        analysis_view(view, df, hit, terms, excl)
        return

    results = exact_search(
        df=df,
        hit=hit,
        text_column=text_column,
        queries=terms,
        max_results=max_results,
        context_chars=context_chars,
        excl=excl,
    )

    st.markdown(
        f'<div class="search-meta">{html.escape(scope)}完全一致: 全 {total:,} 件中 {len(results):,} 件表示 · 文章をクリックで全文表示</div>',
        unsafe_allow_html=True,
    )

    cards = []
    for result in results:
        cards.append(
            f"""
            <details class="result-card">
              <summary>
                <div class="meta">{result['meta']}</div>
                <div class="result-text">{result['snippet']}</div>
              </summary>
              <div class="full-text">{build_full_text(result['text'], result['spans'])}</div>
            </details>
            """
        )

    st.html("".join(cards))


search_section(df, dataset, user)
