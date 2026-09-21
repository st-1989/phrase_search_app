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
  --text: #111111;
  --muted: #a0a0a0;
  --border: #e6e6e6;
  --subtle: #6f6f6f;
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
  padding: 0.95rem 0.1rem 1.0rem 0.1rem;
  border-bottom: 1px solid var(--border);
}

.result-id {
  color: var(--subtle);
  font-size: 0.80rem;
  margin-bottom: 0.35rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.result-text {
  font-size: 1.05rem;
  line-height: 1.85;
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
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
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

uploaded = st.file_uploader("CSVファイル", type=["csv"])

if uploaded is None:
    st.info("CSVファイルをアップロードしてください。")
    st.stop()

try:
    df = load_csv(uploaded.getvalue(), uploaded.name)
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
    text_column = st.selectbox("検索対象の列", string_like_columns)
    id_candidates = ["(行番号を使う)"] + list(df.columns)
    id_column = st.selectbox("表示用ID列", id_candidates)
    context_chars = st.slider("前後の表示文字数", 10, 120, 45, 5)
    max_results = st.slider("最大表示件数", 10, 500, 100, 10)

query = st.text_input(
    "検索フレーズ",
    placeholder="例: 機械学習を用いて",
    help="入力中の文字列を、そのまま完全一致で検索します。",
)

if not query:
    st.write(f"**{len(df):,} 行**を読み込みました。検索語を入力してください。")
    st.stop()

results = exact_search(
    df=df,
    text_column=text_column,
    query=query,
    max_results=max_results,
    context_chars=context_chars,
)

st.markdown(
    f'<div class="search-meta">完全一致: {len(results):,} 件表示 / 最大 {max_results:,} 件</div>',
    unsafe_allow_html=True,
)

if not results:
    st.info("完全一致する句は見つかりませんでした。")
    st.stop()

for result in results:
    row_idx = result["row_idx"]

    if id_column == "(行番号を使う)":
        display_id = f"row {row_idx}"
    else:
        value = df.at[row_idx, id_column]
        display_id = str(value) if not pd.isna(value) else f"row {row_idx}"

    st.markdown(
        f"""
        <div class="result-card">
          <div class="result-id">{html.escape(display_id)} · {result['count']} match(es)</div>
          <div class="result-text">{result['snippet']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("全文を表示"):
        st.write(result["text"])
