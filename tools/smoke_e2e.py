"""서버 전체 흐름 테스트 (데모 모드, API 호출 없음).

업로드 → 내용 파악 → 3장 동시 기획 → ㅇㅋ → 장표 제작(PPTX+미리보기+자체점검) → 빨간 펜 수정 → 채팅
→ (python-pptx 있으면) 직접 수정본 업로드 → 확정 → 에셋 zip → 전체 덱
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):  # Windows 콘솔(cp1252/cp949)에서 한글 출력 오류 방지
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
os.chdir(ROOT)

from studio import store  # noqa: E402

backup = store.SETTINGS_FILE.read_text("utf-8") if store.SETTINGS_FILE.exists() else None
store.SETTINGS_FILE.write_text(json.dumps({"mock": True, "self_check": True}), "utf-8")

import app  # noqa: E402
from studio.llm import _multipart, placeholder_png  # noqa: E402

srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{srv.server_address[1]}"
created = []


def req(path, data=None, files=None, method=None):
    if files is not None or (data is not None and not isinstance(data, (dict, list))):
        body, ct = _multipart(data or {}, files or [])
    elif data is not None:
        body, ct = json.dumps(data).encode(), "application/json"
    else:
        body, ct = None, None
    r = urllib.request.Request(B + path, data=body, method=method or ("POST" if body is not None else "GET"))
    if ct:
        r.add_header("Content-Type", ct)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if "json" in resp.headers.get("Content-Type", "") else raw
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{path}: {e.code} {e.read().decode()}")


def form(path, fields=None, files=None):
    return req(path, fields or {"_": "1"}, files or [])


def wait(pid, cond, what, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = req(f"/api/projects/{pid}")
        if cond(p):
            return p
        time.sleep(0.3)
    raise AssertionError(f"timeout: {what}\n{json.dumps(p, ensure_ascii=False)[:1500]}")


def idle(no):
    return lambda p: p["slides"][str(no)]["job"] is None and p["slides"][str(no)]["stage"] not in (
        "planning", "building", "revising")


try:
    env = req("/api/env")
    print("env:", env)
    tmp = Path(tempfile.mkdtemp())
    pages = []
    for i in range(3):
        f = tmp / f"p{i + 1}.png"
        f.write_bytes(placeholder_png(320, 180))
        pages.append(("source", f))
    ref = tmp / "ref.png"
    ref.write_bytes(placeholder_png(200, 120))
    r = form("/api/projects", {"name": "E2E", "request": "귀엽지만 유치하지 않게"}, pages + [("refs", ref)])
    pid = r["id"]
    created.append(pid)
    p = wait(pid, lambda p: len(p["pages"]) == 3 and p["job"] is None and p["summary"], "ingest")
    assert p["slides"]["1"]["kind"] == "cover"

    print(req(f"/api/projects/{pid}/batch", {"slides": [1, 2, 3], "action": "plan"}))
    p = wait(pid, lambda p: all(p["slides"][k]["stage"] == "plan_review" and not p["slides"][k]["job"]
                                for k in ("1", "2", "3")), "plans")

    form(f"/api/projects/{pid}/slides/2/approve_plan")
    p = wait(pid, idle(2), "build")
    s = p["slides"]["2"]
    assert s["stage"] == "review", (s["stage"], s["error"])
    assert len(s["versions"]) >= 1
    v = s["versions"][-1]
    lay = req(f"/api/projects/{pid}/file/{v['layout']}")
    assert any(e["type"] == "donut" for e in lay["elements"])
    print("built:", len(s["versions"]), "versions; warnings:", s["warnings"][:3])

    marks = [{"x": 5, "y": 5, "w": 40, "h": 15}]
    form(f"/api/projects/{pid}/slides/2/revise",
         {"feedback": "메인 문구 손글씨 느낌 빼고 한 줄로", "save_rule": "project", "marks": json.dumps(marks)})
    p = wait(pid, idle(2), "revise")
    s = p["slides"]["2"]
    assert s["stage"] == "review", s["error"]
    lay2 = req(f"/api/projects/{pid}/file/{s['versions'][-1]['layout']}")
    assert "수정본" in lay2["elements"][0]["text"], lay2["elements"][0]
    assert p["rules_log"], "규칙 저장 안 됨"
    assert "손글씨" in p["guide"]

    ans = form(f"/api/projects/{pid}/slides/2/chat", {"message": "디자인이 왜 AI스러워 보일까?"})
    assert ans["answer"]
    p = req(f"/api/projects/{pid}")
    assert len(p["slides"]["2"]["chat"]) == 2

    if env["python_pptx"]:
        pptx = req(f"/api/projects/{pid}/slides/2/pptx")
        f = tmp / "edited.pptx"
        f.write_bytes(pptx)
        form(f"/api/projects/{pid}/slides/2/import", {"_": "1"}, [("upload", f)])
        p = wait(pid, idle(2), "import")
        s = p["slides"]["2"]
        assert s["versions"][-1]["source"] == "manual", s["error"]

    n = len(p["slides"]["2"]["versions"])
    form(f"/api/projects/{pid}/slides/2/approve", {"version": str(n - 2)})
    p = req(f"/api/projects/{pid}")
    assert p["slides"]["2"]["stage"] == "done" and p["slides"]["2"]["approved"] == n - 2

    z = req(f"/api/projects/{pid}/slides/2/assets.zip")
    assert z[:2] == b"PK"
    if env["python_pptx"]:
        deck = req(f"/api/projects/{pid}/deck.pptx")
        assert deck[:2] == b"PK"
    req(f"/api/projects/{pid}/rules/delete", {"rule": p["rules_log"][0]})
    assert not req(f"/api/projects/{pid}")["rules_log"]
    print("usage:", req(f"/api/projects/{pid}")["usage"])
    print("E2E OK")
finally:
    srv.shutdown()
    for pid in created:
        shutil.rmtree(store.PROJECTS_DIR / pid, ignore_errors=True)
    if backup is None:
        store.SETTINGS_FILE.unlink(missing_ok=True)
    else:
        store.SETTINGS_FILE.write_text(backup, "utf-8")
