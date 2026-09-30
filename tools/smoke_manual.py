"""복붙 모드 흐름 테스트: 가짜 사용자가 복붙 카드를 보고 HTTP 로 답을 넣는다 (API 호출 없음).

- 기획 3장 동시 → 카드 3장
- 제작: 레이아웃 JSON (첫 답은 일부러 망가뜨려서 재요청 확인) → 에셋 이미지 1개 업로드, 나머지 건너뛰기
- 수정 + 규칙 저장 (한 번의 복붙으로 규칙까지)
- 채팅 (동기 요청이 복붙 답을 기다리는지)
- 취소 → 오류 → 다시 시도
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
store.SETTINGS_FILE.write_text(json.dumps({"mode": "manual", "mock": False}), "utf-8")

import app  # noqa: E402
from studio.llm import _mock_chat, _multipart, placeholder_png  # noqa: E402

srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{srv.server_address[1]}"
created = []
tmp = Path(tempfile.mkdtemp())
stats = {"text": 0, "image": 0, "skip": 0, "broken": 0, "notes": 0}
cancel_next = {"on": False}


def call(path, fields=None, files=None, js=None):
    if js is not None:
        body, ct = json.dumps(js).encode(), "application/json"
    elif fields is not None or files is not None:
        body, ct = _multipart(fields or {"_": "1"}, files or [])
    else:
        body, ct = None, None
    r = urllib.request.Request(B + path, data=body, method="POST" if body is not None else "GET")
    if ct:
        r.add_header("Content-Type", ct)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if "json" in resp.headers.get("Content-Type", "") else raw
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{path}: {e.code} {e.read().decode()}")


def fake_user(pid, stop):
    """복붙 카드를 보고 답하는 가짜 사용자."""
    broke_once = False
    while not stop.is_set():
        p = call(f"/api/projects/{pid}")
        for r in p["relay"]:
            if r["kind"] == "text":
                assert "[작업 지침" in r["text"] and "[이번 요청]" in r["text"], r["text"][:200]
            else:
                assert "이미지 1장만" in r["text"], r["text"][:200]
            for i in range(len(r["images"])):
                assert call(f"/api/relay/{r['id']}/image/{i}")[:4] == b"\x89PNG"
            if r["note"]:
                stats["notes"] += 1
            if cancel_next["on"]:
                cancel_next["on"] = False
                call(f"/api/relay/{r['id']}", {"action": "cancel"})
                continue
            if r["kind"] == "image":
                if stats["image"] == 0:
                    f = tmp / "gen.png"
                    f.write_bytes(placeholder_png(128, 128, transparent=True))
                    call(f"/api/relay/{r['id']}", {"action": "answer"}, [("file", f)])
                    stats["image"] += 1
                else:
                    call(f"/api/relay/{r['id']}", {"action": "skip"})
                    stats["skip"] += 1
                continue
            if r["json"] and "LAYOUT_DESIGN" in r["text"] and not broke_once:
                broke_once = True
                stats["broken"] += 1
                call(f"/api/relay/{r['id']}", {"action": "answer", "answer": "네! 아래처럼 배치했어요 (잘림"})
                continue
            ans = _mock_chat("", [r["text"]], r["json"])
            if r["json"]:
                ans = "물론이죠!\n```json\n" + ans + "\n```\n필요하면 말씀해 주세요."  # ChatGPT 스타일 포장
            call(f"/api/relay/{r['id']}", {"action": "answer", "answer": ans})
            stats["text"] += 1
        time.sleep(0.2)


def wait(pid, cond, what, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = call(f"/api/projects/{pid}")
        if cond(p):
            return p
        time.sleep(0.3)
    raise AssertionError(f"timeout: {what}\n{json.dumps(p, ensure_ascii=False)[:1200]}")


def idle(no, stages=("review", "plan_review", "done", "error")):
    return lambda p: not p["slides"][str(no)]["job"] and p["slides"][str(no)]["stage"] in stages


stop = threading.Event()
try:
    srcs = []
    for i in range(3):
        f = tmp / f"p{i + 1}.png"
        f.write_bytes(placeholder_png(320, 180))
        srcs.append(("source", f))
    ref = tmp / "ref.png"
    ref.write_bytes(placeholder_png(200, 120))
    pid = call("/api/projects", {"name": "복붙 테스트", "request": "AI스럽지 않게"}, srcs + [("refs", ref)])["id"]
    created.append(pid)
    threading.Thread(target=fake_user, args=(pid, stop), daemon=True).start()
    wait(pid, lambda p: len(p["pages"]) == 3 and not p["job"] and p["summary"], "ingest")

    call(f"/api/projects/{pid}/batch", js={"slides": [1, 2, 3], "action": "plan"})
    wait(pid, lambda p: all(p["slides"][k]["stage"] == "plan_review" and not p["slides"][k]["job"]
                            for k in "123"), "plans")

    call(f"/api/projects/{pid}/slides/2/approve_plan", {"_": "1"})
    p = wait(pid, idle(2), "build")
    s = p["slides"]["2"]
    assert s["stage"] == "review", s["error"]
    assert len(s["versions"]) == 1, "복붙 모드에서는 자체 점검 생략 → 버전 1개"
    assert stats["broken"] == 1 and stats["notes"] >= 1, stats
    assert (store.pdir(pid) / "slides/02/assets/icon-arrow.png").exists(), "업로드한 에셋 저장 안 됨"

    call(f"/api/projects/{pid}/slides/2/revise", {"feedback": "손글씨 느낌 빼", "save_rule": "project",
                                                  "marks": json.dumps([{"x": 5, "y": 5, "w": 30, "h": 10}])})
    p = wait(pid, lambda p: idle(2)(p) and len(p["slides"]["2"]["versions"]) == 2, "revise")
    assert p["rules_log"] == ["손글씨/낙서 스타일 텍스트 금지"], p["rules_log"]

    ans = call(f"/api/projects/{pid}/slides/2/chat", {"message": "왜 AI스러워?"})
    assert ans["answer"].startswith("(데모)"), ans

    # 취소 → 오류 → 다시 시도
    cancel_next["on"] = True
    call(f"/api/projects/{pid}/slides/3/approve_plan", {"_": "1"})
    p = wait(pid, idle(3), "cancel")
    assert p["slides"]["3"]["stage"] == "error" and "취소" in p["slides"]["3"]["error"], p["slides"]["3"]
    call(f"/api/projects/{pid}/slides/3/retry", {"_": "1"})
    p = wait(pid, idle(3), "retry")
    assert p["slides"]["3"]["stage"] == "review", p["slides"]["3"]["error"]
    print("stats:", stats)
    print("MANUAL OK")
finally:
    stop.set()
    srv.shutdown()
    for pid in created:
        shutil.rmtree(store.PROJECTS_DIR / pid, ignore_errors=True)
    shutil.rmtree(store.PROJECTS_DIR / "_relay", ignore_errors=True)
    if backup is None:
        store.SETTINGS_FILE.unlink(missing_ok=True)
    else:
        store.SETTINGS_FILE.write_text(backup, "utf-8")
