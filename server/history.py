"""ユーザー別の検索履歴。app.py と同じ .search_history.json を読み書きする（形式も同じ）。

画面側の語の条件 id（row / report / or）は、ファイルには app.py と同じ日本語ラベルで保存する。
除外フレーズは "exclude" として追加で保存する（app.py は読み飛ばす）。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / ".search_history.json"
HISTORY_LIMIT = 200
DEFAULT_USER = "ゲスト"
MODE_LABELS = {"row": "両方を含む（同じ文）", "report": "両方を含む（同じレポート内）", "or": "どちらかを含む"}
MODE_IDS = {v: k for k, v in MODE_LABELS.items()}

_lock = threading.Lock()
_last: dict[str, tuple[tuple, float]] = {}  # ユーザーごとの直前の記録（入力途中の上書き判定用）


def _read() -> dict:
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    data["users"].setdefault(DEFAULT_USER, [])
    return data


def _write(data: dict) -> None:
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def _public(data: dict) -> dict:
    users = {}
    for name, entries in data["users"].items():
        users[name] = [
            {
                "query": e.get("query", ""), "query2": e.get("query2", ""),
                "mode": MODE_IDS.get(e.get("mode", ""), ""), "exclude": e.get("exclude", ""),
                "dataset": e.get("dataset", ""), "hits": e.get("hits", 0), "at": e.get("at", ""),
            }
            for e in entries
        ]
    return {"users": users}


def load() -> dict:
    with _lock:
        return _public(_read())


def _key(e: dict) -> tuple:
    return (e.get("query", ""), e.get("query2", ""), e.get("mode", "") if e.get("query2") else "", e.get("exclude", ""))


def _typing_step(a: tuple, b: tuple) -> bool:
    def ext(x: str, y: str) -> bool:
        return x.startswith(y) or y.startswith(x)
    same_rest = a[2] == b[2]
    return same_rest and (
        (a[1] == b[1] and a[3] == b[3] and ext(a[0], b[0]))
        or (a[0] == b[0] and a[3] == b[3] and ext(a[1], b[1]))
        or (a[0] == b[0] and a[1] == b[1] and ext(a[3], b[3]))
    )


def record(entry: dict) -> dict:
    user = str(entry.get("user") or DEFAULT_USER)
    q2 = entry.get("query2", "")
    new = {
        "query": entry.get("query", ""),
        "query2": q2,
        "mode": MODE_LABELS.get(entry.get("mode", ""), "") if q2 else "",
        "dataset": entry.get("dataset", ""),
        "hits": int(entry.get("hits", 0)),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if entry.get("exclude"):
        new["exclude"] = entry["exclude"]
    if not new["query"]:
        return load()
    key = _key(new)
    with _lock:
        data = _read()
        entries = data["users"].setdefault(user, [])
        last = _last.get(user)
        if (last and entries and _key(entries[0]) == last[0] and time.time() - last[1] < 30
                and _typing_step(last[0], key)):
            entries.pop(0)  # 打ちかけの語は置き換える
        entries[:] = [e for e in entries if _key(e) != key]
        entries.insert(0, new)
        del entries[HISTORY_LIMIT:]
        _write(data)
        _last[user] = (key, time.time())
        return _public(data)


def add_user(name: str) -> dict:
    name = name.strip()
    with _lock:
        data = _read()
        if name:
            data["users"].setdefault(name, [])
            _write(data)
        return _public(data)


def remove(user: str, at: str, query: str) -> dict:
    with _lock:
        data = _read()
        entries = data["users"].get(user, [])
        for i, e in enumerate(entries):
            if e.get("at") == at and e.get("query") == query:
                entries.pop(i)
                break
        _write(data)
        return _public(data)


def clear(user: str) -> dict:
    with _lock:
        data = _read()
        data["users"][user] = []
        _last.pop(user, None)
        _write(data)
        return _public(data)
