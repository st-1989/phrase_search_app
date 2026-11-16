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
