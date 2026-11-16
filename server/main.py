"""Phrase Search の API サーバー（FastAPI）。

データの読み込み（CSV / Excel → Parquet キャッシュ）だけを担当し、検索そのものはブラウザ側で行う。
起動: uvicorn server.main:app --port 8000 （リポジトリ直下で実行）
"""
from __future__ import annotations

import io
import json
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .merge import merge_tables

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / ".cache" / "uploads"  # 画面からアップロードしたファイル
DATA_DIRS = [ROOT, ROOT / "data", UPLOAD_DIR]
FILE_PATTERNS = ["*.csv", "*.xlsx", "*.xls"]
CACHE_DIR = ROOT / ".cache"
CACHE_VERSION = "v1"  # app.py と同じキャッシュを共有する
WEB_DIST = ROOT / "web" / "dist"

TEXT_COLUMN = "本文"
YEAR_COLUMN = "年(CDEX)"
EXPECTED_COLUMNS = [
    "発行体ID(業務)", "発行体コード(業務)", "証券コード(CDEX)", YEAR_COLUMN, "公表日(CDEX)",
    "発行体名(CDEX)", "発行体名(業務)", "業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)",
    "公表日(業務)", "発行体格付(業務)", "格付の方向性(業務)", "格付アクション(業務)", "タグ", TEXT_COLUMN,
]
MULTI_FILTER_COLUMNS = ["業種(業務)", "担当部署(業務)", "担当アナリスト(CDEX)", "格付アクション(業務)"]
DATASET_SEP = "|"  # 複数ファイルをまとめたデータセットの id は、各ファイルの id をこれでつなぐ


def list_local_files() -> list[Path]:
    paths: list[Path] = []
    for data_dir in DATA_DIRS:
        if data_dir.is_dir():
            for pattern in FILE_PATTERNS:
                paths.extend(sorted(data_dir.glob(pattern)))
    return paths


def finalize_table(df: pd.DataFrame) -> pd.DataFrame:
    df = df[[c for c in EXPECTED_COLUMNS if c in df.columns]].copy()
    for col in MULTI_FILTER_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype("category")
    if YEAR_COLUMN in df.columns:
        df[YEAR_COLUMN] = pd.to_numeric(df[YEAR_COLUMN], errors="coerce").astype("Int64")
    return df


def parse_table(file_bytes: bytes, filename: str) -> pd.DataFrame:
    wanted = set(EXPECTED_COLUMNS)
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(file_bytes), dtype=str, usecols=lambda c: c in wanted)
    last_error: Exception | None = None
    for enc in ["utf-8-sig", "utf-8", "cp932", "shift_jis"]:
        for engine in ("pyarrow", "c"):
            try:
                kwargs = {"usecols": lambda c: c in wanted} if engine == "c" else {}
                return pd.read_csv(io.BytesIO(file_bytes), encoding=enc, dtype=str, engine=engine, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
    raise RuntimeError(f"CSVを読み込めませんでした: {last_error}")


def load_local_file(src: Path) -> pd.DataFrame:
    mtime_ns = src.stat().st_mtime_ns
    stem = f"{src.stem}-{src.suffix[1:]}"
    cache_file = CACHE_DIR / f"{stem}-{mtime_ns}-{CACHE_VERSION}.parquet"
    if cache_file.exists():
        try:
            return pd.read_parquet(cache_file)
        except Exception:  # noqa: BLE001
            cache_file.unlink(missing_ok=True)
    df = finalize_table(parse_table(src.read_bytes(), src.name))
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        for old in CACHE_DIR.glob(f"{stem}-*.parquet"):
            old.unlink(missing_ok=True)
        df.to_parquet(cache_file)
    except Exception:  # noqa: BLE001
        pass
    return df


def encode_column(s: pd.Series) -> dict:
    """列を JSON にする。値の種類が少ない列は辞書（values）＋番号（codes）で送り、転送量を減らす。"""
    def clean(v):
        if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
            return None
        return int(v) if hasattr(v, "item") and not isinstance(v, str) else v

    codes, uniques = pd.factorize(s, use_na_sentinel=True)
    if len(uniques) <= max(1, len(s) // 4):
        return {"values": [clean(u) for u in uniques], "codes": codes.tolist()}
    return {"raw": [clean(v) for v in s.astype(object).tolist()]}


DatasetKey = tuple[tuple[str, int], ...]  # ((ファイルのパス, 更新時刻), ...)


@lru_cache(maxsize=4)
def cached_merged(key: DatasetKey) -> tuple[pd.DataFrame, list[dict]]:
    """読み込んだ表を覚えておく（分析のたびに読み直さない）。読み取り専用で使う。"""
    tables = []
    for path_str, _ in key:
        df = load_local_file(Path(path_str))
        if TEXT_COLUMN not in df.columns:
            raise HTTPException(400, f"{Path(path_str).name}: 「{TEXT_COLUMN}」列が見つかりません。")
        tables.append((Path(path_str).name, df))
    return merge_tables(tables)


def cached_table(key: DatasetKey) -> pd.DataFrame:
    return cached_merged(key)[0]


@lru_cache(maxsize=4)
def dataset_payload(key: DatasetKey) -> bytes:
    df, info = cached_merged(key)
    body = {
        "rows": len(df),
        "columns": list(df.columns),
        "data": {c: encode_column(df[c]) for c in df.columns},
        "sources": info,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


app = FastAPI(title="Phrase Search API")
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _datasets() -> dict[str, Path]:
    return {str(p.relative_to(ROOT)): p for p in list_local_files()}


@app.get("/api/datasets")
def datasets() -> list[dict]:
    return [{"id": k, "size": p.stat().st_size} for k, p in _datasets().items()]


def _resolve(id: str) -> DatasetKey:
    """データセットの id（複数ファイルなら DATASET_SEP でつないだもの）を、ファイルと更新時刻の組にする。"""
    known = _datasets()
    ids = list(dict.fromkeys(x for x in id.split(DATASET_SEP) if x))
    if not ids or any(x not in known for x in ids):
        raise HTTPException(404, "データセットが見つかりません。")
    return tuple((str(known[x]), known[x].stat().st_mtime_ns) for x in ids)


@app.get("/api/dataset")
def dataset(id: str):
    payload = dataset_payload(_resolve(id))
    return Response(content=payload, media_type="application/json")


@app.post("/api/upload")
async def upload(request: Request, name: str):
    """CSV / Excel を受け取り、.cache/uploads/ に保存してデータセット一覧に加える。"""
    fname = Path(name).name
    if not fname.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "CSV または Excel（.xlsx / .xls）を選んでください。")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / fname
    dest.write_bytes(await request.body())
    try:
        df = load_local_file(dest)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"読み込めませんでした: {exc}")
    if TEXT_COLUMN not in df.columns:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"「{TEXT_COLUMN}」列が見つかりません。")
    return {"id": str(dest.relative_to(ROOT))}


# ---- 共起語 ----

from . import analysis as an  # noqa: E402
from . import history as hist  # noqa: E402


def _analysis(spec: dict) -> an.Analysis:
    key = _resolve(spec.get("dataset", ""))
    if not [t for t in spec.get("terms", []) if t]:
        raise HTTPException(400, "検索フレーズがありません。")
    return an.get_analysis(key, cached_table(key), spec)


@app.get("/api/capabilities")
def capabilities():
    return {"janome": an.JANOME_AVAILABLE}


@app.post("/api/terms")
def terms(spec: dict = Body(...)):
    return _analysis(spec).terms()


@app.post("/api/group_terms")
def group_terms(spec: dict = Body(...)):
    return _analysis(spec).group_terms(spec.get("group_cols") or [an.RATING_COLUMN])


@app.post("/api/term_context")
def term_context(spec: dict = Body(...), term: str = Body(...), group: str | None = Body(None)):
    a = _analysis(spec)
    if group and group not in a.groups:
        a.group_terms(spec.get("group_cols") or [an.RATING_COLUMN])
    return a.context(term, group)


# ---- ユーザー別の検索履歴（app.py と同じ .search_history.json を共有） ----

@app.get("/api/history")
def history_get():
    return hist.load()


@app.post("/api/history/user")
def history_add_user(name: str = Body(..., embed=True)):
    return hist.add_user(name)


@app.post("/api/history/record")
def history_record(entry: dict = Body(...)):
    return hist.record(entry)


@app.post("/api/history/remove")
def history_remove(user: str = Body(...), at: str = Body(...), query: str = Body(...)):
    return hist.remove(user, at, query)


@app.post("/api/history/clear")
def history_clear(user: str = Body(..., embed=True)):
    return hist.clear(user)


# ビルド済みの画面（web/dist）があれば、同じポートで配信する
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIST / "index.html")
