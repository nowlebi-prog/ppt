"""프로젝트 저장소: 모든 데이터는 로컬 폴더(projects/<id>/)에 저장된다."""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

if getattr(sys, "frozen", False):  # exe 로 실행 중
    RES = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))   # 내장 리소스
    ROOT = Path(sys.executable).resolve().parent                          # 데이터는 exe 옆 폴더
else:
    RES = ROOT = Path(__file__).resolve().parent.parent

PROJECTS_DIR = ROOT / "projects"
PROMPTS_DIR = ROOT / "prompts"
RULES_DIR = ROOT / "rules"
SETTINGS_FILE = ROOT / "settings.json"
WEB_DIR = RES / "web"

PROJECTS_DIR.mkdir(exist_ok=True)


def _sync_editable_files():
    """프롬프트/규칙은 사용자가 고칠 수 있게 exe 옆에 복사해 둔다.
    - 사용자가 안 고친 파일 → 새 버전으로 자동 교체
    - 사용자가 고친 파일 → 유지 (덮어쓰지 않음)
    """
    import hashlib
    manifest_f = ROOT / "prompts" / ".shipped.json"
    try:
        manifest = json.loads(manifest_f.read_text("utf-8"))
    except Exception:
        manifest = {}

    def h(p: Path) -> str:
        return hashlib.sha1(p.read_bytes()).hexdigest()

    old_marker = re.compile(r"\A(DECK_SUMMARY|DESIGN_GUIDE|LAYOUT_DESIGN|LAYOUT_REVISE|SELF_CHECK|SLIDE_CHAT)\s*\n"
                            r"|\{\{GLOBAL_RULES\}\}")
    changed = False
    for name in ("prompts", "rules"):
        src, dst = RES / name, ROOT / name
        if src == dst or not src.exists():
            continue
        dst.mkdir(exist_ok=True)
        for f in src.glob("*.md"):
            target = dst / f.name
            key = f"{name}/{f.name}"
            new_hash = h(f)
            if not target.exists():
                shutil.copy(f, target)
            else:
                cur = h(target)
                untouched = manifest.get(key) == cur or (
                    key not in manifest and old_marker.search(target.read_text("utf-8", "replace")))
                if cur != new_hash and untouched:
                    shutil.copy(f, target)
                elif cur != new_hash:
                    continue  # 사용자가 고친 파일은 유지 (manifest 도 그대로)
            manifest[key] = new_hash
            changed = True
    if changed:
        manifest_f.parent.mkdir(exist_ok=True)
        manifest_f.write_text(json.dumps(manifest, indent=1), "utf-8")


_sync_editable_files()

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()

# 장표 단계 (UI에 그대로 노출됨)
STAGES = {
    "todo": "대기",
    "planning": "AI 기획 중",
    "plan_review": "기획 컨펌 대기",
    "building": "AI 장표 제작 중",
    "review": "장표 확인 대기",
    "revising": "AI 수정 중",
    "done": "완료",
    "error": "오류",
}
USER_TURN = {"plan_review", "review", "error"}

DEFAULT_SETTINGS = {
    "mode": "manual",        # manual = 복붙 (ChatGPT 등 구독으로 직접) | api = API 자동
    "api_key": "",
    "base_url": "https://api.openai.com/v1",
    "text_model": "gpt-5.5",
    "image_model": "gpt-image-2",
    "image_quality": "high",
    "batch_size": 3,
    "self_check": True,      # 미리보기를 AI가 먼저 보고 규칙 위반을 1회 자동 수정
    "max_assets": 6,         # 장표당 생성 에셋 최대 개수
    "mock": False,
}


def _lock(pid: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.RLock())


def pdir(pid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        raise ValueError("잘못된 프로젝트 id")
    return PROJECTS_DIR / pid


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        s.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
    return s


def save_settings(data: dict) -> dict:
    s = load_settings()
    for k in DEFAULT_SETTINGS:
        if k in data and data[k] is not None:
            s[k] = data[k]
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")
    return s


def create_project(name: str, request: str) -> dict:
    pid = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    d = pdir(pid)
    for sub in ("source", "pages", "refs", "assets", "slides"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(RULES_DIR / "guide_template.md", d / "guide.md")
    proj = {
        "id": pid,
        "name": name or "새 프로젝트",
        "request": request or "",
        "created": time.time(),
        "ingest": {"state": "none", "message": ""},
        "template": "",          # 원본 PPTX (테마/마스터 유지용)
        "pages": [],
        "slides": {},
        "rules_log": [],
        "usage": {"text_calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0},
        "job": None,
        "error": "",
    }
    save(pid, proj)
    return proj


def list_projects() -> list[dict]:
    out = []
    for f in sorted(PROJECTS_DIR.glob("*/project.json"), reverse=True):
        try:
            p = json.loads(f.read_text("utf-8"))
            out.append({"id": p["id"], "name": p["name"], "pages": len(p["pages"]),
                        "done": sum(1 for s in p["slides"].values() if s["stage"] == "done")})
        except Exception:
            continue
    return out


def load(pid: str) -> dict:
    with _lock(pid):
        return json.loads((pdir(pid) / "project.json").read_text("utf-8"))


def save(pid: str, proj: dict) -> None:
    with _lock(pid):
        f = pdir(pid) / "project.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(proj, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(f)


def update(pid: str, fn):
    """원자적 load → 수정 → save."""
    with _lock(pid):
        proj = load(pid)
        result = fn(proj)
        save(pid, proj)
        return result


def new_slide(no: int, kind: str = "normal") -> dict:
    return {
        "no": no,
        "kind": kind,            # cover | normal
        "stage": "todo",
        "job": None,
        "error": "",
        "keep_game_images": True,
        "plan": "",
        "plan_versions": [],
        # 버전 = 실제 PPTX 1개. {layout, pptx, preview, images, feedback, source(ai|manual), ts}
        "versions": [],
        "approved": -1,          # 확정한 버전 index
        "warnings": [],
        "chat": [],              # [{role, text, ts}]
        "log": [],
    }


def slide_dir(pid: str, no: int) -> Path:
    d = pdir(pid) / "slides" / f"{int(no):02d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_text(path: Path, default: str = "") -> str:
    return path.read_text("utf-8") if path.exists() else default


def prompt(name: str) -> str:
    return read_text(PROMPTS_DIR / f"{name}.md")


def rel(pid: str, p: Path) -> str:
    return str(Path(p).resolve().relative_to(pdir(pid).resolve())).replace("\\", "/")


def safe_path(pid: str, rel_path: str) -> Path:
    base = pdir(pid).resolve()
    p = (base / rel_path).resolve()
    if base not in p.parents and p != base:
        raise ValueError("잘못된 경로")
    return p
