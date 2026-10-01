"""OpenAI 호환 API 클라이언트 (표준 라이브러리만 사용).

- chat(): 텍스트 + 이미지 입력 → 텍스트/JSON
- image(): 이미지 생성 (참고 이미지가 있으면 edits 엔드포인트 사용)
- settings["mock"] 이 True 면 API 호출 없이 가짜 결과를 돌려준다 (UI 테스트용).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import struct
import time
import urllib.error
import urllib.request
import uuid
import zlib
from pathlib import Path


class LLMError(RuntimeError):
    pass


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _post(settings: dict, endpoint: str, body: bytes, content_type: str, timeout: int = 600) -> dict:
    if not settings.get("api_key"):
        raise LLMError("설정에서 API 키를 입력해 주세요. (또는 '데모 모드'를 켜세요)")
    url = settings["base_url"].rstrip("/") + endpoint
    last = None
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            last = LLMError(f"API 오류 {e.code}: {msg[:600]}")
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(3 * (attempt + 1))
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = LLMError(f"네트워크 오류: {e}")
            time.sleep(3 * (attempt + 1))
    raise last


# 사용량 기록 콜백: pipeline 이 설정 (kind, info)
on_usage = None


def _meter(kind: str, info: dict):
    if on_usage:
        try:
            on_usage(kind, info)
        except Exception:
            pass


def chat(settings: dict, system: str, parts: list, json_mode: bool = False,
         history: list[dict] | None = None, task: str = "") -> str:
    """parts: 문자열 또는 Path(이미지) 의 리스트. history: [{role: user|assistant, text}] (이전 대화).
    task: 작업 종류 (summary, plan, layout, revise, selfcheck, chat, guide) — 데모 응답/복붙 카드 표시에 사용."""
    if settings.get("mock"):
        _meter("text", {})
        return _mock_chat(task, json_mode)
    if settings.get("mode") == "manual":
        return _manual_chat(system, parts, json_mode, history, task=task)
    content = [{"type": "text", "text": str(p)} for p in parts if isinstance(p, str) and p]
    imgs = compact_images([p for p in parts if isinstance(p, Path) and p.exists()], max_single=6)
    if imgs:
        content.append({"type": "text", "text": "[첨부 이미지 — 순서대로]\n" + "\n".join(
            f"{k + 1}. {d}" for k, (_, d) in enumerate(imgs))})
    for p, _ in imgs:
        content.append({"type": "image_url", "image_url": {"url": _data_url(p), "detail": "high"}})
    messages = [{"role": "system", "content": system}]
    for h in history or []:
        messages.append({"role": "assistant" if h["role"] in ("ai", "assistant") else "user", "content": h["text"]})
    messages.append({"role": "user", "content": content})
    body = {"model": settings["text_model"], "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    res = _post(settings, "/chat/completions", json.dumps(body).encode(), "application/json")
    u = res.get("usage") or {}
    _meter("text", {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0)})
    try:
        return res["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"예상치 못한 응답: {str(res)[:400]}")


def parse_json(txt: str) -> dict:
    txt = (txt or "").strip()
    a, b = txt.find("{"), txt.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("JSON 없음")
    return json.loads(txt[a: b + 1])


def chat_json(settings: dict, system: str, parts: list, task: str = "") -> dict:
    manual = settings.get("mode") == "manual" and not settings.get("mock")
    note = ""
    for attempt in range(3 if manual else 1):
        txt = (_manual_chat(system, parts, True, None, note, task=task) if manual
               else chat(settings, system, parts, json_mode=True, task=task))
        try:
            return parse_json(txt)
        except Exception as e:
            note = (f"⚠️ 방금 붙여넣은 답에서 JSON을 읽지 못했어요 ({e}). ChatGPT 답변의 코드블록 오른쪽 위 "
                    "[복사] 버튼으로 JSON 전체를 복사해서 다시 붙여넣어 주세요. 잘렸다면 ChatGPT에 '계속'이라고 보내서 이어 받으세요.")
            last = txt
    raise LLMError(f"JSON 파싱 실패: {last[:400]}")


# ---------------------------------------------------------------- 복붙 모드

def describe(p: Path) -> tuple[str, str]:
    """첨부 이미지 경로 → (분류, 설명)."""
    parts = Path(p).parts
    name = Path(p).stem
    if "pages" in parts:
        return "pages", f"{int(name) if name.isdigit() else name}번 장표 원본"
    if "refs" in parts:
        return "refs", "톤앤매너 레퍼런스"
    if "feedback" in parts:
        return "other", "사용자가 빨간 펜으로 표시한 현재 장표"
    if "slides" in parts and name.startswith("v") and name[1:].isdigit():
        return "other", f"현재 장표 미리보기 (v{name[1:]})"
    if "assets" in parts and "slides" not in parts:
        return "assets", f"실제 자산 ({Path(p).name})"
    return "other", Path(p).name


def compact_images(imgs: list[Path], max_single: int = 3) -> list[tuple[Path, str]]:
    """복붙 모드: 이미지가 많으면 분류별로 번호 붙은 모음 시트로 합친다 (드래그 횟수 최소화)."""
    items = [(p, *describe(p)) for p in imgs]
    if len(items) <= max_single:
        return [(p, d) for p, _, d in items]
    try:
        from . import sheets, store
        out_dir = store.PROJECTS_DIR / "_relay" / "sheets"
    except Exception:
        return [(p, d) for p, _, d in items]
    result, done = [], set()
    for p, cat, desc in items:
        if cat in done:
            continue
        group = [(q, dd) for q, c, dd in items if c == cat]
        if cat == "other" or len(group) == 1:
            if cat != "other":
                done.add(cat)
                result.append(group[0])
            else:
                result.append((p, desc))
            continue
        done.add(cat)
        if cat == "pages":
            labels = [(q, dd.replace(" 원본", "")) for q, dd in group]
        else:
            labels = [(q, f"{k + 1}") for k, (q, _) in enumerate(group)]
        try:
            files = sheets.contact_sheets(labels, out_dir)
        except Exception:
            result += group
            continue
        for k, f in enumerate(files):
            sub_ = labels[k * 9:(k + 1) * 9]
            if cat == "pages":
                nums = [lab.replace("번 장표", "") for _, lab in sub_]
                result.append((f, f"원본 장표 모음 ({nums[0]}~{nums[-1]}번, 각 칸 위에 번호)"))
            elif cat == "refs":
                result.append((f, f"톤앤매너 레퍼런스 모음 ({len(sub_)}장)"))
            else:
                result.append((f, f"실제 자산 모음 ({len(sub_)}장)"))
    return result


def _manual_chat(system: str, parts: list, json_mode: bool, history: list[dict] | None, note: str = "",
                 task: str = "") -> str:
    from . import relay
    texts = [p for p in parts if isinstance(p, str) and p]
    imgs = compact_images([p for p in parts if isinstance(p, Path) and p.exists()])
    out = []
    if history:
        out.append("[지금까지 이 장표에 대해 나눈 대화]")
        out += [f"{'나' if h['role'] == 'user' else '너'}: {h['text']}" for h in history]
        out.append("")
    out += texts
    if imgs:
        out.append("\n[첨부 이미지 — 순서대로]")
        out += [f"{k + 1}. {d}" for k, (_, d) in enumerate(imgs)]
    if json_mode:
        out.append("\n※ 설명 없이 ```json 코드블록 하나로만 답해줘.")
    if system.strip():
        out += ["", "━━━━━━━━━━━━━━━━━━━━", "[참고: 작업 규칙 · 디자인 가이드 — 위 요청을 할 때 반드시 지킬 것]", system.strip()]
    return relay.ask("text", "\n".join(out), [p for p, _ in imgs], want_json=json_mode, note=note,
                     task=task, descs=[d for _, d in imgs])


def _multipart(fields: dict, files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = "----studio" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, path in files:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def image(settings: dict, prompt: str, out: Path, refs: list[Path] | None = None,
          transparent: bool = False, size: str | None = None) -> Path:
    """이미지 1장 생성 → out 에 PNG 저장."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if settings.get("mode") == "manual" and not settings.get("mock"):
        from . import relay
        guide = (f"아래 설명대로 이미지 1장만 만들어줘. 크기 {size or '1024x1024'}"
                 + (", 투명 배경 PNG" if transparent else "") + ". 이미지 안에 글자는 넣지 마.\n\n")
        ref_items = compact_images([r for r in (refs or []) if r.exists()][:4], max_single=1)
        got = relay.ask("image", guide + prompt + ("\n\n첨부 이미지는 스타일 참고용이야." if ref_items else ""),
                        [p for p, _ in ref_items], task="asset", descs=[d for _, d in ref_items])
        import shutil
        shutil.copy(got, out)
        return out
    _meter("image", {})
    if settings.get("mock"):
        out.write_bytes(placeholder_png(640 if not transparent else 256, 360 if not transparent else 256,
                                        transparent=transparent))
        return out
    size = size or "1024x1024"
    refs = [r for r in (refs or []) if r.exists()][:16]
    fields = {"model": settings["image_model"], "prompt": prompt[:30000], "size": size,
              "quality": settings.get("image_quality", "high"), "n": "1"}
    if transparent:
        fields["background"] = "transparent"
        fields["output_format"] = "png"

    def call(f):
        if refs:
            body, ct = _multipart(f, [("image[]", r) for r in refs])
            return _post(settings, "/images/edits", body, ct)
        return _post(settings, "/images/generations", json.dumps(f).encode(), "application/json")

    try:
        res = call(fields)
    except LLMError as e:
        # 일부 모델은 투명 배경/품질 옵션을 지원하지 않는다 → 옵션 빼고 재시도
        if "background" in str(e) or "quality" in str(e) or "output_format" in str(e):
            for k in ("background", "output_format", "quality"):
                fields.pop(k, None)
            res = call(fields)
        else:
            raise
    item = (res.get("data") or [{}])[0]
    if item.get("b64_json"):
        out.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as r:
            out.write_bytes(r.read())
    else:
        raise LLMError(f"이미지 응답 없음: {str(res)[:400]}")
    return out


# ---------------------------------------------------------------- 데모 모드

def placeholder_png(w: int, h: int, transparent: bool = False) -> bytes:
    """외부 라이브러리 없이 단색 PNG 생성."""
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            if transparent:
                inside = (x - w / 2) ** 2 + (y - h / 2) ** 2 < (w / 3) ** 2
                raw += bytes((255, 107, 44, 255 if inside else 0))
            else:
                edge = x < 4 or y < 4 or x >= w - 4 or y >= h - 4
                raw += bytes((231, 231, 228)) if edge else bytes((250, 249, 246))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ctype = 6 if transparent else 2
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _mock_layout(revised: bool) -> dict:
    main = "데모 메인 문구입니다 (수정본)" if revised else "데모 메인 문구입니다"
    return {"background": "#FFFFFF", "elements": [
        {"id": "e1", "type": "text", "x": 6, "y": 8, "w": 80, "h": 10, "text": main, "size": 32, "bold": True,
         "color": "#111111"},
        {"id": "e2", "type": "text", "x": 6, "y": 19, "w": 80, "h": 6, "size": 16, "color": "#747474",
         "runs": [{"text": "신규 유저 1명당 광고비는 "}, {"text": "134원", "color": "#FF6B2C", "bold": True},
                  {"text": "입니다."}]},
        {"id": "e3", "type": "line", "x1": 6, "y1": 30, "x2": 94, "y2": 30, "color": "#E7E7E4", "width": 1},
        {"id": "e4", "type": "donut", "x": 8, "y": 36, "w": 22, "h": 40, "values": [1, 4],
         "labels": ["성공", "나머지"], "colors": ["#FF6B2C", "#E7E7E4"]},
        {"id": "e5", "type": "text", "x": 8, "y": 78, "w": 22, "h": 6, "text": "신작 5개 중 1개", "size": 13,
         "align": "center", "color": "#111111"},
        {"id": "e6", "type": "rect", "x": 38, "y": 36, "w": 56, "h": 48, "fill": "#FFFFFF", "line": "#E7E7E4",
         "line_w": 0.75, "radius": 0.04},
        {"id": "e7", "type": "bar", "x": 42, "y": 44, "w": 48, "h": 36, "categories": ["방문 X", "방문 O"],
         "values": [1, 2.8], "colors": ["#C9C9C6", "#FF6B2C"], "horizontal": True, "number_format": "0.0\"배\""},
        {"id": "e8", "type": "text", "x": 42, "y": 38, "w": 40, "h": 5, "text": "친구 농장 방문 D7 잔존",
         "size": 14, "bold": True},
        {"id": "e9", "type": "image", "x": 88, "y": 8, "w": 6, "h": 10, "source": "gen:icon-arrow",
         "prompt": "thin line arrow icon, orange #FF6B2C", "kind": "icon"},
        {"id": "e10", "type": "line", "x1": 6, "y1": 90, "x2": 40, "y2": 90, "color": "#747474", "dash": True,
         "arrow": True},
    ]}


def _mock_chat(task: str, json_mode: bool) -> str:
    time.sleep(0.3)
    if json_mode and task == "layout":
        return json.dumps(_mock_layout(False), ensure_ascii=False)
    if json_mode and task == "revise":
        return json.dumps({**_mock_layout(True), "rules": ["손글씨/낙서 스타일 텍스트 금지"]}, ensure_ascii=False)
    if json_mode and task == "selfcheck":
        return json.dumps({"ok": True, "issues": []}, ensure_ascii=False)
    if json_mode:
        return json.dumps({"rules": []}, ensure_ascii=False)
    if task == "chat":
        return "(데모) 메인 문구가 두 줄로 넘어가서 위계가 약해 보여요. 30pt로 줄이고 서브와 간격을 넓히면 좋겠습니다."
    if task == "guide":
        return "# 디자인 가이드 (데모)\n\n- 배경: #FFFFFF\n- 폰트: Pretendard\n- 포인트: #FF6B2C\n"
    if task == "summary":
        return "## 핵심 메시지\n(데모) 덱 요약입니다.\n\n## 장표 구성\n1. Cover\n2. Team\n"
    return ("## 이 장표의 역할\n(데모) 투자자가 3초 안에 핵심을 이해하게 만든다.\n\n"
            "## 메인 / 서브\n- 메인: 데모 메인 문구입니다\n- 서브: 데모 서브 문구입니다\n\n"
            "## 레이아웃\n좌측 도넛 + 우측 비교 막대\n\n## 에셋\n- 생성: 얇은 화살표 아이콘\n- 생성 금지: 게임 화면\n")
