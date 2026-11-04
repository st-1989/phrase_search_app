import html
import inspect
import io
import json
import re
import unicodedata
from datetime import datetime
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

.meta {
  color: var(--subtle);
  font-size: 0.82rem;
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
  font-size: 0.95rem;
  line-height: 1.6;
  word-break: break-word;
}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


APP_DIR = Path(__file__).parent
DATA_DIRS = [APP_DIR, APP_DIR / "data"]
UPLOAD_OPTION = "ファイルをアップロード…"
FILE_PATTERNS = ["*.csv", "*.xlsx", "*.xls"]

# 入力ファイルのフォーマット（この列名で読み込む。検索対象は TEXT_COLUMN のみ）
TEXT_COLUMN = "本文"
EXPECTED_COLUMNS = [
    "発行体ID", "発行体コード", "年", "公表日", "発行体名(CDEX)", "発行体名(業務)",
    "業種", "担当部署", "担当アナリスト", "公表日", "発行体格付", "格付の方向性",
    "格付アクション", "タグ", TEXT_COLUMN,
]
# フィルタに使う列（サイドバー）
FILTER_COLUMNS = ["年", "業種", "担当部署", "格付アクション"]


def list_local_files() -> list[Path]:
    paths = []
    for data_dir in DATA_DIRS:
        if data_dir.is_dir():
            for pattern in FILE_PATTERNS:
                paths.extend(sorted(data_dir.glob(pattern)))
    return paths


@st.cache_data(show_spinner=False, max_entries=20)
def load_local_file(path: str, mtime: float) -> pd.DataFrame:
    return load_table(Path(path).read_bytes(), Path(path).name)


@st.cache_data(show_spinner=False, max_entries=20)
def load_table(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSV / Excel を読み込む。ID・コードの先頭ゼロを保つため全列を文字列で読む。"""
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            return pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        except Exception as exc:
            raise RuntimeError(f"Excelを読み込めませんでした: {exc}")
    last_error = None
    for enc in ["utf-8-sig", "utf-8", "cp932", "shift_jis"]:
        try:
            return pd.read_csv(io.BytesIO(file_bytes), encoding=enc, dtype=str)
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

    rating = " ".join(x for x in [v("発行体格付"), v("格付の方向性")] if x)
    items = [
        ("", f"<b>{v('発行体名(CDEX)')}</b>"),
        ("年", v("年")),
        ("公表日", v("公表日")),
        ("業種", v("業種")),
        ("格付", rating),
        ("アクション", v("格付アクション")),
        ("担当", v("担当アナリスト")),
        ("タグ", v("タグ")),
    ]
    return " · ".join(f"{label} {val}".strip() for label, val in items if val and val != "<b></b>")


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
                "meta": meta_html(df.loc[row_idx]),
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
        df = load_table(uploaded.getvalue(), uploaded.name)
    else:
        path = dataset_labels[dataset]
        df = load_local_file(str(path), path.stat().st_mtime)
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

    st.header("設定")
    context_chars = st.slider("前後の表示文字数", 10, 120, 45, 5)
    max_results = st.slider("最大表示件数", 10, 500, 100, 10)

    st.subheader("絞り込み")
    mask = pd.Series(True, index=df.index)
    for col in FILTER_COLUMNS:
        if col not in df.columns:
            continue
        options = sorted(df[col].dropna().unique())
        chosen = st.multiselect(col, options)
        if chosen:
            mask &= df[col].isin(chosen)
    df = df[mask]

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
        placeholder="例: 機械学習を用いて",
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

    results = exact_search(
        df=df,
        text_column=text_column,
        query=query,
        max_results=max_results,
        context_chars=context_chars,
    )

    if user and user_unchanged:
        record_search(user, query, dataset, len(results))

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
                <div class="meta">{result['meta']}</div>
                <div class="result-text">{result['snippet']}</div>
              </summary>
              <div class="full-text">{build_full_text(result['text'], result['spans'])}</div>
            </details>
            """
        )

    st.html("".join(cards))


search_section(df, dataset, user)
