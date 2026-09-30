"""PPT Studio — 로컬 실행 서버.  python app.py  →  http://localhost:8765"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import traceback
import webbrowser
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from studio import pipeline, relay, store

for _s in (sys.stdout, sys.stderr):  # Windows 콘솔(cp1252/cp949)에서 한글 출력 오류 방지
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

WEB = store.WEB_DIR
PORT = int(os.environ.get("PORT", "8765"))


def parse_multipart(headers, body: bytes) -> tuple[dict, list[tuple[str, str, bytes]]]:
    """반환: (fields, [(field, filename, data)])"""
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
    fields, files = {}, []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        fname = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if fname:
            if data:
                files.append((name, Path(fname).name, data))
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


def save_uploads(pid: str, files, kind: str) -> list[Path]:
    d = store.pdir(pid) / kind
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for _, fname, data in files:
        safe = re.sub(r"[^\w.\-가-힣 ]", "_", fname)
        p = d / safe
        p.write_bytes(data)
        out.append(p)
    return out


def project_view(pid: str) -> dict:
    p = store.load(pid)
    d = store.pdir(pid)
    p["summary"] = store.read_text(d / "summary.md")
    p["guide"] = store.read_text(d / "guide.md")
    p["refs"] = [f"refs/{f.name}" for f in pipeline._images(d / "refs")]
    p["user_assets"] = [f"assets/{f.name}" for f in pipeline._images(d / "assets")]
    p["sources"] = [f.name for f in sorted((d / "source").glob("*"))]
    p["stages"] = store.STAGES
    p["user_turn"] = sorted(store.USER_TURN)
    p["has_deck"] = (d / "deck.pptx").exists()
    p["relay"] = relay.listing(pid)
    return p


def env_info() -> dict:
    import platform
    from studio import preview
    font_dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts",
                 Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path.home() / ".fonts",
                 Path("/usr/share/fonts")]
    font = any(f.exists() and any(f.rglob("Pretendard*")) for f in font_dirs if str(f) not in ("", "."))
    try:
        import pptx  # noqa: F401
        has_pptx = True
    except ImportError:
        has_pptx = False
    return {"os": platform.system(), "pretendard": font, "python_pptx": has_pptx, "preview": preview.available()}


class H(BaseHTTPRequestHandler):
    server_version = "PPTStudio/1.0"

    def log_message(self, fmt, *args):
        if "/api/projects/" in (args[0] if args else "") and '"GET' in fmt % args:
            return  # 폴링 로그 생략
        sys.stderr.write("[studio] " + fmt % args + "\n")

    # ---------------- 응답 헬퍼
    def send(self, code=200, data=None, ctype="application/json; charset=utf-8", headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def file(self, path: Path, download: str | None = None):
        if not path.exists() or not path.is_file():
            return self.send(404, {"error": "파일 없음"})
        headers = {}
        if download:
            from urllib.parse import quote
            headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(download)}"
        self.send(200, path.read_bytes(), mimetypes.guess_type(str(path))[0] or "application/octet-stream", headers)

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self) -> dict:
        b = self.body()
        return json.loads(b.decode("utf-8")) if b else {}

    def form(self):
        ct = self.headers.get("Content-Type", "")
        if ct.startswith("multipart/"):
            return parse_multipart(self.headers, self.body())
        return self.json_body(), []

    # ---------------- 라우팅
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        path = unquote(urlparse(self.path).path)
        try:
            if method == "GET" and path in ("/", "/index.html"):
                return self.file(WEB / "index.html")
            if method == "GET" and path.startswith("/static/"):
                return self.file(WEB / Path(path[len("/static/"):]).name)
            if path == "/api/settings":
                if method == "POST":
                    store.save_settings(self.json_body())
                s = store.load_settings()
                s["api_key"] = ("●●●●" + s["api_key"][-4:]) if s["api_key"] else ""
                return self.send(200, s)
            mr = re.fullmatch(r"/api/relay/(\w+)(?:/image/(\d+))?", path)
            if mr:
                rid = mr.group(1)
                if method == "GET" and mr.group(2) is not None:
                    return self.file(relay.image_path(rid, int(mr.group(2))))
                if method == "POST":
                    fields, files = self.form()
                    act = fields.get("action", "answer")
                    f = None
                    if files:
                        _, fname, data = files[0]
                        f = relay.save_upload(rid, data, Path(fname).suffix.lower(), store.ROOT / "projects" / "_relay")
                    relay.respond(rid, answer=fields.get("answer"), file=f, skip=act == "skip", cancel=act == "cancel")
                    return self.send(200, {"ok": True})
            if path == "/api/env":
                return self.send(200, env_info())
            if path == "/api/projects":
                if method == "GET":
                    return self.send(200, store.list_projects())
                fields, files = self.form()
                proj = store.create_project(fields.get("name", ""), fields.get("request", ""))
                pid = proj["id"]
                save_uploads(pid, [f for f in files if f[0] == "refs"], "refs")
                save_uploads(pid, [f for f in files if f[0] == "assets"], "assets")
                src = save_uploads(pid, [f for f in files if f[0] == "source"], "source")
                if src:
                    store.update(pid, lambda p: p.update(ingest={"state": "running", "message": ""}))
                    pipeline.run_job(pid, None, "원본 분석 + 내용 파악", pipeline.do_ingest, [str(s) for s in src])
                return self.send(200, {"id": pid})

            m = re.fullmatch(r"/api/projects/([\w-]+)(/.*)?", path)
            if not m:
                return self.send(404, {"error": "not found"})
            pid, rest = m.group(1), m.group(2) or ""

            if method == "GET":
                if rest == "":
                    return self.send(200, project_view(pid))
                if rest.startswith("/file/"):
                    return self.file(store.safe_path(pid, rest[len("/file/"):]))
                if rest == "/deck.pptx":
                    out = pipeline.build_deck(pid, include_review="all=1" in (urlparse(self.path).query or ""))
                    name = store.load(pid)["name"]
                    return self.file(out, download=f"{name}.pptx")
                mz = re.fullmatch(r"/slides/(\d+)/assets\.zip", rest)
                if mz:
                    no = int(mz.group(1))
                    return self.send(200, pipeline.assets_zip(pid, no), "application/zip", {
                        "Content-Disposition": f"attachment; filename=slide-{no:02d}-assets.zip"})
                mp = re.fullmatch(r"/slides/(\d+)/pptx", rest)
                if mp:
                    f = pipeline.slide_pptx(pid, int(mp.group(1)))
                    return self.file(f, download=f.name)
                return self.send(404, {"error": "not found"})

            # ---- POST
            if rest == "/meta":
                b = self.json_body()
                store.update(pid, lambda p: p.update({k: b[k] for k in ("name", "request") if k in b}))
                return self.send(200, {"ok": True})
            if rest == "/upload":
                fields, files = self.form()
                kind = fields.get("kind", "refs")
                if kind not in ("refs", "assets", "source"):
                    return self.send(400, {"error": "kind"})
                saved = save_uploads(pid, files, kind)
                if kind == "source" and saved:
                    allsrc = sorted((store.pdir(pid) / "source").glob("*"))
                    pipeline.run_job(pid, None, "원본 분석 + 내용 파악", pipeline.do_ingest, [str(s) for s in allsrc])
                return self.send(200, {"ok": True, "count": len(saved)})
            if rest == "/delete_file":
                rel = self.json_body().get("path", "")
                if not rel.startswith(("refs/", "assets/")):
                    return self.send(400, {"error": "삭제 불가"})
                store.safe_path(pid, rel).unlink(missing_ok=True)
                return self.send(200, {"ok": True})
            if rest == "/summary":
                pipeline.run_job(pid, None, "내용 파악", pipeline.do_summary)
                return self.send(200, {"ok": True})
            if rest == "/guide/build":
                pipeline.run_job(pid, None, "디자인 가이드 작성", pipeline.do_guide)
                return self.send(200, {"ok": True})
            if rest == "/guide":
                (store.pdir(pid) / "guide.md").write_text(self.json_body().get("text", ""), "utf-8")
                return self.send(200, {"ok": True})
            if rest == "/rules/delete":
                pipeline.remove_rule(pid, self.json_body().get("rule", ""))
                return self.send(200, {"ok": True})
            if rest == "/batch":
                b = self.json_body()
                results = {}
                for no in b.get("slides", []):
                    try:
                        pipeline.act(pid, int(no), b.get("action", "plan"))
                        results[no] = "ok"
                    except Exception as e:
                        results[no] = str(e)
                return self.send(200, results)
            ms = re.fullmatch(r"/slides/(\d+)/(\w+)", rest)
            if ms:
                no, action = int(ms.group(1)), ms.group(2)
                fields, files = self.form()
                if action == "plan_edit":
                    store.update(pid, lambda p: p["slides"][str(no)].update(plan=fields.get("plan", "")))
                    return self.send(200, {"ok": True})
                if action == "options":
                    def fn(p):
                        s = p["slides"][str(no)]
                        if "keep_game_images" in fields:
                            s["keep_game_images"] = str(fields["keep_game_images"]).lower() in ("1", "true")
                        if fields.get("kind") in ("cover", "normal"):
                            s["kind"] = fields["kind"]
                    store.update(pid, fn)
                    return self.send(200, {"ok": True})
                if action == "chat":
                    msg = (fields.get("message") or "").strip()
                    if not msg:
                        return self.send(400, {"error": "메시지를 입력해 주세요"})
                    return self.send(200, {"answer": pipeline.chat(pid, no, msg)})
                saved = {}
                for field, fname, data in files:
                    sub = "uploads" if field == "upload" else "feedback"
                    d = store.slide_dir(pid, no) / sub
                    d.mkdir(exist_ok=True)
                    target = d / f"{len(list(d.iterdir())) + 1:02d}{Path(fname).suffix.lower() or '.png'}"
                    target.write_bytes(data)
                    saved[field] = store.rel(pid, target)
                marks = json.loads(fields["marks"]) if fields.get("marks") else []
                ver = int(fields["version"]) if str(fields.get("version", "")).lstrip("-").isdigit() else None
                pipeline.act(pid, no, action, feedback=fields.get("feedback", ""), annotated=saved.get("annotated", ""),
                             save_rule=fields.get("save_rule", ""), marks=marks, version=ver,
                             upload=saved.get("upload", ""))
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "not found"})
        except (ValueError, RuntimeError, KeyError, FileNotFoundError) as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": str(e)})


def main():
    pipeline.recover_stale()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://localhost:{PORT}"
    print(f"\n  PPT Studio 실행 중 → {url}\n  (종료: 이 창에서 Ctrl+C)\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
