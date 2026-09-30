"""복붙 모드: AI 요청을 '복붙 대기함'에 올리고, 사용자가 ChatGPT 등에 붙여넣은 뒤 답을 가져올 때까지 기다린다.

파이프라인 입장에서는 llm.chat() 이 조금 오래 걸리는 것과 같아서, 나머지 로직은 그대로 동작한다.
"""
from __future__ import annotations

import io
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path

from . import store

ctx = threading.local()   # 현재 스레드의 작업 맥락 (pid, no, label, step)

_pending: dict[str, dict] = {}
_lock = threading.Lock()


class Cancelled(RuntimeError):
    pass


class Skipped(RuntimeError):
    pass


def _folder(rid: str) -> Path:
    return store.PROJECTS_DIR / "_relay" / rid


def ask(kind: str, text: str, images: list[Path], want_json: bool = False, note: str = "", task: str = "",
        descs: list[str] | None = None) -> str:
    """kind: text | image. 반환: 텍스트 답변 또는 업로드된 이미지 경로."""
    rid = uuid.uuid4().hex[:10]
    ev = threading.Event()
    imgs = [Path(p) for p in images if p and Path(p).exists()]
    descs = list(descs or [])[:len(imgs)] + [p.name for p in imgs[len(descs or []):]]
    # 한 번에 끌어다 놓을 수 있게 번호 붙인 사본 폴더 준비
    folder = _folder(rid)
    folder.mkdir(parents=True, exist_ok=True)
    copies = []
    for k, (p, d) in enumerate(zip(imgs, descs), 1):
        safe = re.sub(r'[\\/:*?"<>|()~,]+', "", d).strip().replace(" ", "_")[:40]
        dst = folder / f"{k:02d}_{safe}{p.suffix.lower() or '.png'}"
        shutil.copy(p, dst)
        copies.append(dst)
    item = {
        "id": rid, "kind": kind, "task": task, "pid": getattr(ctx, "pid", None), "no": getattr(ctx, "no", None),
        "label": getattr(ctx, "label", "") or "AI 요청", "step": getattr(ctx, "step", ""),
        "text": text, "images": [str(c) for c in copies], "descs": descs,
        "json": want_json, "note": note, "created": time.time(),
        "_event": ev, "answer": None, "file": None, "skip": False, "cancel": False,
    }
    with _lock:
        _pending[rid] = item
    try:
        while not ev.wait(1.0):
            pass
    finally:
        with _lock:
            _pending.pop(rid, None)
        if kind == "text" or not item.get("file"):
            shutil.rmtree(folder, ignore_errors=True)
    if item["cancel"]:
        raise Cancelled("복붙 요청을 취소했습니다. [다시 시도]를 누르면 이어서 진행합니다.")
    if item["skip"]:
        raise Skipped("건너뜀")
    return item["file"] if kind == "image" else (item["answer"] or "")


def respond(rid: str, answer: str | None = None, file: Path | None = None, skip: bool = False,
            cancel: bool = False) -> None:
    with _lock:
        item = _pending.get(rid)
    if not item:
        raise ValueError("이미 처리됐거나 없는 요청입니다")
    if cancel:
        item["cancel"] = True
    elif skip:
        item["skip"] = True
    elif item["kind"] == "image":
        if not file:
            raise ValueError("생성한 이미지를 올려주세요")
        item["file"] = str(file)
    else:
        if not (answer or "").strip():
            raise ValueError("답변을 붙여넣어 주세요")
        item["answer"] = answer
    item["_event"].set()


def listing(pid: str | None = None) -> list[dict]:
    with _lock:
        items = [dict(i) for i in _pending.values() if pid is None or i["pid"] == pid]
    out = []
    for i in sorted(items, key=lambda x: x["created"]):
        i.pop("_event", None)
        i["images"] = [Path(p).name for p in i["images"]]
        out.append(i)
    return out


def image_path(rid: str, idx: int) -> Path:
    with _lock:
        item = _pending.get(rid)
    if not item or not (0 <= idx < len(item["images"])):
        raise ValueError("이미지 없음")
    return Path(item["images"][idx])


def open_folder(rid: str) -> None:
    """탐색기/Finder 로 이미지 폴더 열기 → 전부 선택해서 ChatGPT 에 한 번에 끌어다 놓기."""
    with _lock:
        if rid not in _pending:
            raise ValueError("이미 처리됐거나 없는 요청입니다")
    folder = _folder(rid)
    system = platform.system()
    if system == "Windows":
        import os
        os.startfile(str(folder))  # noqa
    elif system == "Darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def images_zip(rid: str) -> bytes:
    with _lock:
        item = _pending.get(rid)
    if not item:
        raise ValueError("이미 처리됐거나 없는 요청입니다")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in item["images"]:
            z.write(f, Path(f).name)
    return buf.getvalue()


def save_upload(rid: str, data: bytes, suffix: str, tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    f = tmp_dir / f"relay-{rid}{suffix or '.png'}"
    f.write_bytes(data)
    return f


def cancel_all(pid: str | None = None):
    for i in listing(pid):
        try:
            respond(i["id"], cancel=True)
        except ValueError:
            pass

