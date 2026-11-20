"""複数の CSV / Excel を1つの表にまとめる（app.py と server/main.py で共通）。pandas 以外に依存しない。"""
from __future__ import annotations

import pandas as pd

TEXT_COLUMN = "本文"
YEAR_COLUMN = "年(CDEX)"
SOURCE_COLUMN = "ファイル"  # 複数のファイルをまとめたとき、行の元のファイル名
EXPECTED_COLUMNS = [
    "発行体ID(業務)", "発行体コード(業務)", "証券コード(CDEX)", YEAR_COLUMN, "公表日(CDEX)",
    "発行体名(CDEX)", "発行体名(業務)", "業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)",
    "公表日(業務)", "発行体格付(業務)", "格付の方向性(業務)", "格付アクション(業務)", "タグ", TEXT_COLUMN,
]
CATEGORY_COLUMNS = ["業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)", "格付アクション(業務)", SOURCE_COLUMN]


def dedup_key_columns(df: pd.DataFrame) -> list[str]:
    """重複とみなす行の判定に使う列（発行体ID＋公表日＋本文。ID・公表日が無ければ、全列）。"""
    date = next((c for c in ("公表日(業務)", "公表日(CDEX)") if c in df.columns), None)
    if "発行体ID(業務)" in df.columns and date:
        return ["発行体ID(業務)", date, TEXT_COLUMN]
    return [c for c in df.columns if c != SOURCE_COLUMN]


def merge_tables(tables: list[tuple[str, pd.DataFrame]]) -> tuple[pd.DataFrame, list[dict]]:
    """複数の表を縦につなぐ。前のファイルにすでにある行（dedup_key_columns が同じ行）は、後のファイルから除く。

    同じファイルの中の重複はそのまま残す。列の名前で揃え、無い列は欠損にする。
    返り値: (まとめた表, ファイルごとの {name, rows, dropped})
    """
    if len(tables) == 1:
        name, df = tables[0]
        return df, [{"name": name, "rows": len(df), "dropped": 0}]
    parts = []
    for i, (name, df) in enumerate(tables):
        part = df.copy()
        for col in part.columns:
            if isinstance(part[col].dtype, pd.CategoricalDtype):
                part[col] = part[col].astype(object)
        part[SOURCE_COLUMN] = name
        part["__src"] = i
        parts.append(part)
    df = pd.concat(parts, ignore_index=True)
    keys = dedup_key_columns(df.drop(columns="__src"))
    first_src = df.groupby(keys, sort=False, dropna=False)["__src"].transform("min")
    keep = (df["__src"] == first_src).to_numpy()
    info = [
        {"name": name, "rows": int(((df["__src"] == i) & keep).sum()), "dropped": int(((df["__src"] == i) & ~keep).sum())}
        for i, (name, _) in enumerate(tables)
    ]
    df = df[keep].drop(columns="__src").reset_index(drop=True)
    cols = [c for c in EXPECTED_COLUMNS if c in df.columns] + [SOURCE_COLUMN]
    df = df[cols]
    for col in CATEGORY_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("category")
    if YEAR_COLUMN in df.columns:
        df[YEAR_COLUMN] = pd.to_numeric(df[YEAR_COLUMN], errors="coerce").astype("Int64")
    return df, info


# ---- 前回のレポートとの比較（格付・方向性の変化） ----
RATING_COLUMN = "発行体格付(業務)"
OUTLOOK_COLUMN = "格付の方向性(業務)"
PREV_RATING_COLUMN = "前回格付"
RATING_CHANGE_COLUMN = "格付の変化"
OUTLOOK_CHANGE_COLUMN = "方向性の変化"
RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
                "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"]
OUTLOOK_ORDER = ["ポジティブ", "安定的", "ネガティブ"]  # 良い順
RATING_CHANGE_ORDER = ["引き上げ", "引き下げ", "据え置き", "前回なし"]
OUTLOOK_CHANGE_ORDER = ["上向き", "下向き", "その他の変更", "変化なし", "前回なし"]
ISSUER_KEY_COLUMNS = ["発行体ID(業務)", "発行体コード(業務)", "発行体名(CDEX)"]
DATE_COLUMNS = ["公表日(業務)", "公表日(CDEX)"]


def _clean_label(s: pd.Series) -> pd.Series:
    """全角→半角・【】と空白を除く（「【AA－】」「AA-」を同じ値にする）。"""
    s = s.astype("string").str.normalize("NFKC").str.replace(r"[【】\s]", "", regex=True)
    s = s.str.replace("−", "-").str.replace("－", "-")
    return s.mask(s == "")


def _date_number(s: pd.Series) -> pd.Series:
    """公表日を YYYYMMDD の数にする（「2025/3/5」「2025-03-05 00:00:00」などに対応）。"""
    p = s.astype("string").str.extract(r"(\d{4})\D(\d{1,2})\D(\d{1,2})").astype("float")
    return p[0] * 10000 + p[1] * 100 + p[2]


def add_change_columns(df: pd.DataFrame) -> pd.DataFrame:
    """発行体ごとに公表日順に並べ、1つ前のレポートと比べた「前回格付」「格付の変化」「方向性の変化」を加える。

    - レポート = 発行体（ID、無ければコード・名前）＋公表日。同じレポートの文はすべて同じ値になる。
    - 格付の変化: 格付の順位（AAA→D）で比べて 引き上げ / 引き下げ / 据え置き。前のレポートが無い・格付が読めないときは 前回なし。
    - 方向性の変化: ポジティブ＞安定的＞ネガティブ で比べて 上向き / 下向き / 変化なし。
      どちらかがこの3つ以外（レーティング・モニターなど）で値が違えば その他の変更。
    - 比べるのは読み込んだデータの中だけ（前年分が無ければ 前回なし）。
    """
    issuer = next((c for c in ISSUER_KEY_COLUMNS if c in df.columns), None)
    date = next((c for c in DATE_COLUMNS if c in df.columns), None)
    if issuer is None or date is None or RATING_COLUMN not in df.columns:
        return df
    w = pd.DataFrame({
        "issuer": df[issuer].astype("string"),
        "date": _date_number(df[date]),
        "rating": _clean_label(df[RATING_COLUMN]),
        "outlook": _clean_label(df[OUTLOOK_COLUMN]) if OUTLOOK_COLUMN in df.columns else pd.NA,
    })
    ok = w["issuer"].notna() & w["date"].notna()
    # レポートごとに1行（最初に出てくる格付・方向性）
    rep = (w[ok].groupby(["issuer", "date"], sort=True)
           .agg(rating=("rating", "first"), outlook=("outlook", "first")).reset_index())
    rep["prev_rating"] = rep.groupby("issuer")["rating"].shift(1)
    rep["prev_outlook"] = rep.groupby("issuer")["outlook"].shift(1)
    has_prev = rep.groupby("issuer").cumcount() > 0

    rank = {r: i for i, r in enumerate(RATING_ORDER)}
    r_now, r_prev = rep["rating"].map(rank), rep["prev_rating"].map(rank)
    rep["rating_change"] = "前回なし"
    known = has_prev & r_now.notna() & r_prev.notna()
    rep.loc[known & (r_now < r_prev), "rating_change"] = "引き上げ"
    rep.loc[known & (r_now > r_prev), "rating_change"] = "引き下げ"
    rep.loc[known & (r_now == r_prev), "rating_change"] = "据え置き"

    orank = {o: i for i, o in enumerate(OUTLOOK_ORDER)}
    o_now, o_prev = rep["outlook"].map(orank), rep["prev_outlook"].map(orank)
    rep["outlook_change"] = "前回なし"
    both = has_prev & rep["outlook"].notna() & rep["prev_outlook"].notna()
    rep.loc[both & (rep["outlook"] == rep["prev_outlook"]), "outlook_change"] = "変化なし"
    ranked = both & o_now.notna() & o_prev.notna()
    rep.loc[ranked & (o_now < o_prev), "outlook_change"] = "上向き"
    rep.loc[ranked & (o_now > o_prev), "outlook_change"] = "下向き"
    rep.loc[both & ~ranked & (rep["outlook"] != rep["prev_outlook"]), "outlook_change"] = "その他の変更"

    m = w[["issuer", "date"]].merge(
        rep[["issuer", "date", "prev_rating", "rating_change", "outlook_change"]], on=["issuer", "date"], how="left")
    out = df.copy()
    out[PREV_RATING_COLUMN] = m["prev_rating"].astype(object).where(m["prev_rating"].notna(), None).to_numpy()
    out[RATING_CHANGE_COLUMN] = pd.Categorical(m["rating_change"].fillna("前回なし"), categories=RATING_CHANGE_ORDER)
    if OUTLOOK_COLUMN in df.columns:
        out[OUTLOOK_CHANGE_COLUMN] = pd.Categorical(m["outlook_change"].fillna("前回なし"), categories=OUTLOOK_CHANGE_ORDER)
    return out
