import html
import re
import unicodedata
from pathlib import Path

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
  max-width: 1100px;
  padding-top: 2rem;
  padding-bottom: 4rem;
}

.search-meta {
  color: var(--subtle);
  font-size: 0.92rem;
  margin: 0.2rem 0 0.8rem 0;
}

.result-card {
  padding: 0.4rem 0.1rem;
  border-bottom: 1px solid var(--border);
}

.result-text {
  font-size: 1.0rem;
  line-height: 1.55;
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

.full-text {
  margin-top: 0.4rem;
  padding: 0.5rem 0.8rem;
  background: rgba(128, 128, 128, 0.08);
  border-left: 3px solid var(--border);
  font-size: 0.95rem;
  line-height: 1.6;
  word-break: break-word;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


APP_DIR = Path(__file__).parent
DATA_DIRS = [APP_DIR, APP_DIR / "data"]
UPLOAD_OPTION = "CSVをアップロード…"


def list_local_csvs() -> list[Path]:
    paths = []
    for data_dir in DATA_DIRS:
        if data_dir.is_dir():
            paths.extend(sorted(data_dir.glob("*.csv")))
    return paths


@st.cache_data(show_spinner=False, max_entries=20)
def load_local_csv(path: str, mtime: float) -> pd.DataFrame:
    return load_csv(Path(path).read_bytes(), Path(path).name)


@st.cache_data(show_spinner=False, max_entries=20)
def load_csv(file_bytes: bytes, filename: str) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]
    last_error = None
    for enc in encodings:
        try:
            return pd.read_csv(pd.io.common.BytesIO(file_bytes), encoding=enc)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"CSVを読み込めませんでした: {last_error}")


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


def exact_search(
    df: pd.DataFrame,
    text_column: str,
    query: str,
    max_results: int,
    context_chars: int,
) -> list[dict]:
    results = []

    for row_idx, value in df[text_column].items():
        if pd.isna(value):
            continue

        text = str(value)
        spans = find_all_literal(text, query)
        if not spans:
            continue

        results.append(
            {
                "row_idx": row_idx,
                "text": text,
                "spans": spans,
                "snippet": build_snippet(text, spans, context_chars=context_chars),
                "count": len(spans),
            }
        )

        if len(results) >= max_results:
            break

    return results


st.title("Japanese Phrase Search")
st.caption("CSV内の文章から、入力した日本語フレーズの完全一致をリアルタイム検索します。")

local_csvs = list_local_csvs()
dataset_labels = {str(path.relative_to(APP_DIR)): path for path in local_csvs}
dataset = st.selectbox("データセット", list(dataset_labels) + [UPLOAD_OPTION])

try:
    if dataset == UPLOAD_OPTION:
        uploaded = st.file_uploader("CSVファイル", type=["csv"])
        if uploaded is None:
            st.info("CSVファイルをアップロードしてください。")
            st.stop()
        df = load_csv(uploaded.getvalue(), uploaded.name)
    else:
        path = dataset_labels[dataset]
        df = load_local_csv(str(path), path.stat().st_mtime)
except Exception as exc:
    st.error(str(exc))
    st.stop()

if df.empty:
    st.warning("CSVにデータがありません。")
    st.stop()

string_like_columns = [
    col for col in df.columns
    if pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col])
]

if not string_like_columns:
    st.error("文章として検索できる文字列列が見つかりませんでした。")
    st.stop()

with st.sidebar:
    st.header("設定")
    default_text_column = max(
        string_like_columns,
        key=lambda col: df[col].fillna("").astype(str).str.len().mean(),
    )
    text_column = st.selectbox(
        "検索対象の列",
        string_like_columns,
        index=string_like_columns.index(default_text_column),
    )
    context_chars = st.slider("前後の表示文字数", 10, 120, 45, 5)
    max_results = st.slider("最大表示件数", 10, 500, 100, 10)


@st.fragment
def search_section(df: pd.DataFrame) -> None:
    query = st.text_input(
        "検索フレーズ",
        placeholder="例: 機械学習を用いて",
        help="入力中の文字列を、そのまま完全一致で検索します。",
        live=True,
    )

    if not query:
        st.write(f"**{len(df):,} 行**を読み込みました。検索語を入力してください。")
        return

    results = exact_search(
        df=df,
        text_column=text_column,
        query=query,
        max_results=max_results,
        context_chars=context_chars,
    )

    st.markdown(
        f'<div class="search-meta">完全一致: {len(results):,} 件表示 / 最大 {max_results:,} 件 · 文章をクリックで全文表示</div>',
        unsafe_allow_html=True,
    )

    if not results:
        st.info("完全一致する句は見つかりませんでした。")
        return

    cards = []
    for result in results:
        cards.append(
            f"""
            <details class="result-card">
              <summary>
                <div class="result-text">{result['snippet']}</div>
              </summary>
              <div class="full-text">{build_full_text(result['text'], result['spans'])}</div>
            </details>
            """
        )

    st.html("".join(cards))


search_section(df)
