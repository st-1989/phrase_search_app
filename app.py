import html
import inspect
import io
import json
import re
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


def record_search(user: str, query: str, dataset: str, hits: int) -> None:
    """検索を履歴に記録する。即時検索では1文字ごとに呼ばれるため、
    直前の語の延長・短縮（入力途中）なら直前の履歴を置き換え、同じ語は先頭に寄せる。"""
    data = load_history()
    entries = data["users"].setdefault(user, [])
    if entries and entries[0]["query"] == query and entries[0]["dataset"] == dataset and entries[0]["hits"] == hits:
        return
    last = st.session_state.get("_last_recorded")
    if (
        last and last[0] == user and entries and entries[0]["query"] == last[1]
        and (query.startswith(last[1]) or last[1].startswith(query))
    ):
        entries.pop(0)
    entries[:] = [e for e in entries if e["query"] != query]
    entries.insert(0, {
        "query": query,
        "dataset": dataset,
        "hits": hits,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    del entries[HISTORY_LIMIT:]
    save_history(data)
    st.session_state["_last_recorded"] = (user, query)


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


def set_query(query: str) -> None:
    st.session_state["query"] = query


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


def find_hits(df: pd.DataFrame, text_column: str, query: str) -> np.ndarray:
    """全行を一括（ベクトル化）で完全一致判定した真偽配列。"""
    return df[text_column].str.contains(query, regex=False, na=False).to_numpy()


def exact_search(
    df: pd.DataFrame,
    hit: np.ndarray,
    text_column: str,
    query: str,
    max_results: int,
    context_chars: int,
) -> list[dict]:
    """ヒットした上位 max_results 件だけ Python でスパン計算・HTML 化する。"""
    subset = df[hit].head(max_results)
    results = []
    for pos in range(len(subset)):
        row = subset.iloc[pos]
        text = str(row[text_column])
        spans = find_all_literal(text, query)
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


def doc_token_sets(texts, method: str) -> list[frozenset]:
    memo = _token_memo()
    fn = _tokens_janome if method == TOKENIZER_JANOME else _tokens_fast
    out = []
    for text in texts:
        key = (method, hash(text))
        if key not in memo:
            memo[key] = fn(text)
        out.append(memo[key])
    return out


def sample_positions(positions: np.ndarray, n: int, seed: int = 0) -> np.ndarray:
    if len(positions) <= n:
        return positions
    return np.sort(np.random.default_rng(seed).choice(positions, n, replace=False))


def enriched_terms(fg, bg, query, exclude, min_count, top_n, rank_by) -> pd.DataFrame:
    """fg（注目する文書群）に多い語を、bg（比較対象）との 2x2 対数尤度比 G² で評価する。"""
    n_fg, n_bg = len(fg), len(bg)
    fg_count, bg_count = Counter(), Counter()
    for t in fg:
        fg_count.update(t)
    for t in bg:
        bg_count.update(t)
    terms = [
        w for w, c in fg_count.items()
        if c >= min_count and w not in exclude and query not in w and w not in query
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


def analysis_view(view: str, df: pd.DataFrame, hit: np.ndarray, query: str) -> None:
    texts = df[text_column]
    hit_pos = np.flatnonzero(hit)

    group_col = RATING_COLUMN
    if view in (VIEW_COUNT, VIEW_GROUP_TERMS):
        options = [c for c in GROUP_COLUMNS if c in df.columns]
        if not options:
            st.info("集計に使える列がありません。")
            return
        group_col = st.selectbox("集計の軸", options, key="group_col")

    if view == VIEW_COUNT:
        g = df[group_col]
        table = pd.DataFrame({"全文書数": g.value_counts(), "一致件数": g[hit].value_counts()}).fillna(0).astype(int)
        order = group_order(table.index, group_col)
        table = table.loc[order].rename_axis(group_col).reset_index()
        table["一致率"] = table["一致件数"] / table["全文書数"]
        chart_data = table.rename(columns={group_col: "group", "一致件数": "count"})
        chart_data["group"] = chart_data["group"].astype(str)
        chart = alt.Chart(chart_data).mark_bar().encode(
            x=alt.X("group:N", sort=[str(o) for o in order], title=group_col, axis=alt.Axis(labelAngle=0)),
            y=alt.Y("count:Q", title="一致件数"),
            tooltip=["group", "count"],
        ).properties(height=300).configure(font=ui["family"] or "sans-serif").configure_axis(
            labelFontSize=ui["size"], titleFontSize=ui["size"]
        )
        st.altair_chart(chart, use_container_width=True)
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={"一致率": st.column_config.NumberColumn(format="percent")})
        return

    with st.expander("分析の設定", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        methods = [TOKENIZER_FAST] + ([TOKENIZER_JANOME] if _JanomeTokenizer else [])
        method = c1.selectbox("語の切り出し", methods, key="tok_method",
                              help="Janome は精度が高い反面、遅いです。初回の解析結果は記憶されます。")
        rank_by = c2.selectbox("並び順", [RANK_BY_SCORE, RANK_BY_COUNT], key="rank_by")
        top_n = c3.slider("表示する語数", 10, 100, 30, 5, key="top_n")
        min_count = c4.slider("最小出現文書数", 1, 20, 3, key="min_count")
        max_docs = c1.slider("分析に使う最大文書数", 100, 3000, 500, 100, key="max_docs",
                             help="一致文書・比較対象それぞれ、この件数まで無作為に抽出して解析します。")
        exclude = set(c2.text_input("除外する語（スペース区切り）", key="exclude").split())
    if _JanomeTokenizer is None:
        st.caption("Janome が未インストールのため高速モードのみ利用できます（`pip install janome` で形態素解析が使えます）。")

    fg_pos = sample_positions(hit_pos, max_docs)

    if view == VIEW_TERMS:
        with st.spinner("共起語を解析中…"):
            fg = doc_token_sets(texts.iloc[fg_pos], method)
            bg = doc_token_sets(texts.iloc[sample_positions(np.arange(len(df)), max_docs, seed=1)], method)
            table = enriched_terms(fg, bg, query, exclude, min_count, top_n, rank_by)
        st.caption(f"一致文書 {len(fg_pos):,} 件（全 {len(hit_pos):,} 件から抽出）を、全文書の無作為標本 {len(bg):,} 件と比較しています。")
        if table.empty:
            st.info("条件に合う語がありませんでした。最小出現文書数を下げてみてください。")
            return
        st.dataframe(table, hide_index=True, width="stretch", column_config=TERM_COLUMN_CONFIG)
        return

    # VIEW_GROUP_TERMS: 軸の値ごとに、一致文書内で特徴的な語（比較対象は一致文書全体）
    with st.spinner("軸ごとの共起語を解析中…"):
        all_fg = doc_token_sets(texts.iloc[fg_pos], method)
        group_values = df[group_col].to_numpy()[fg_pos]
        columns, skipped = {}, []
        for value in group_order(pd.unique(group_values), group_col):
            idx = np.flatnonzero(group_values == value)
            if len(idx) < max(min_count, 3):
                skipped.append(f"{value}（{len(idx)}件）")
                continue
            fg = [all_fg[i] for i in idx]
            table = enriched_terms(fg, all_fg, query, exclude, min_count, top_n, rank_by)
            columns[f"{value}（{len(idx)}件）"] = [
                f"{r.語} ({r.出現文書数})" for r in table.itertuples()
            ] if not table.empty else []
    st.caption(
        f"{group_col}ごとに、一致文書（{len(fg_pos):,} 件）の中でその区分に特に多い語を表示します。語の後ろの数字は出現文書数です。"
        if rank_by == RANK_BY_SCORE else
        f"{group_col}ごとに、一致文書の中で出現文書数が多い語を表示します。語の後ろの数字は出現文書数です。"
    )
    if not columns:
        st.info("件数が足りず表示できませんでした。最小出現文書数を下げるか、絞り込みを緩めてください。")
        return
    height = max(len(v) for v in columns.values())
    st.dataframe(
        pd.DataFrame({k: v + [""] * (height - len(v)) for k, v in columns.items()}, index=range(1, height + 1)),
        width="stretch",
    )
    if skipped:
        st.caption("件数が少ないため省略: " + "、".join(skipped))


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
    max_results = st.slider("最大表示件数", 10, 500, 100, 10)

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

    for col in MULTI_FILTER_COLUMNS:
        if col not in df.columns:
            continue
        options = sorted(df[col].dropna().unique())
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
        c1.button(e["query"], key=f"recent_{i}", on_click=set_query, args=(e["query"],), use_container_width=True)
        c2.caption(f'{e["at"]} · {e["hits"]:,} 件 · {e["dataset"]}')

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
    query = st.text_input(
        "検索フレーズ",
        placeholder="例: 比較的強い",
        help="入力中の文字列を、そのまま完全一致で検索します。",
        key="query",
        **live_kwargs,
    )

    # ユーザー切替直後の再実行では、前のユーザーの検索語を記録しない
    user_unchanged = st.session_state.get("_seen_user") == user
    st.session_state["_seen_user"] = user

    if not query:
        st.write(f"**{len(df):,} 行**を読み込みました。検索語を入力してください。")
        recent_searches(user)
        return

    hit = find_hits(df, text_column, query)
    total = int(hit.sum())

    if user and user_unchanged:
        record_search(user, query, dataset, total)

    if total == 0:
        st.markdown('<div class="search-meta">完全一致: 全 0 件</div>', unsafe_allow_html=True)
        st.info("完全一致する句は見つかりませんでした。")
        return

    view = st.radio(
        "表示", [VIEW_RESULTS, VIEW_COUNT, VIEW_TERMS, VIEW_GROUP_TERMS],
        horizontal=True, key="view", label_visibility="collapsed",
    )
    if view != VIEW_RESULTS:
        st.markdown(
            f'<div class="search-meta">完全一致: 全 {total:,} 件 / {len(df):,} 文書</div>',
            unsafe_allow_html=True,
        )
        analysis_view(view, df, hit, query)
        return

    results = exact_search(
        df=df,
        hit=hit,
        text_column=text_column,
        query=query,
        max_results=max_results,
        context_chars=context_chars,
    )

    st.markdown(
        f'<div class="search-meta">完全一致: 全 {total:,} 件中 {len(results):,} 件表示 · 文章をクリックで全文表示</div>',
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
