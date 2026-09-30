"""작업 루틴 엔진 (PPTX 우선).

장표 1장의 흐름:
  todo → [AI] 기획 → plan_review(ㅇㅋ/수정)
       → [AI] 레이아웃 설계 → 에셋 생성 → PPTX 렌더 → 미리보기 PNG → (AI 자체 점검 1회)
       → review (미리보기 = 실제 PPTX. 빨간 펜 / 피드백 / 채팅 / 직접 고친 PPTX 업로드)
       → [AI] 수정 → review … → ㅇㅋ → done
"""
from __future__ import annotations

import io
import json
import shutil
import threading
import time
import traceback
import zipfile
from pathlib import Path

from . import factcheck, llm, preview, relay, store
from .ingest import IMG_EXT, ingest
from .render_pptx import DEFAULT_H_PT, DEFAULT_W_PT, Renderer, normalize, overflow_warnings, palette_from_guide

MAX_REFS = 6
_tl = threading.local()


# ------------------------------------------------------------ 사용량 기록

def _usage(kind: str, info: dict):
    pid = getattr(_tl, "pid", None)
    if not pid:
        return

    def fn(p):
        u = p.setdefault("usage", {"text_calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0})
        if kind == "text":
            u["text_calls"] += 1
            u["input_tokens"] += int(info.get("in", 0) or 0)
            u["output_tokens"] += int(info.get("out", 0) or 0)
        else:
            u["images"] += 1
    store.update(pid, fn)


llm.on_usage = _usage


# ------------------------------------------------------------ 공통 헬퍼

def _images(d: Path, limit: int = 99) -> list[Path]:
    return sorted(p for p in d.glob("*") if p.suffix.lower() in IMG_EXT)[:limit] if d.exists() else []


def deck_text(proj: dict) -> str:
    return "\n\n".join(f"[{p['no']}번 장표]\n{p['text']}" for p in proj["pages"])


def page(proj: dict, no: int) -> dict:
    for p in proj["pages"]:
        if p["no"] == int(no):
            return p
    return {"no": no, "image": "", "text": ""}


def fill(tpl: str, **kw) -> str:
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v if v is not None else ""))
    return tpl


def system_prompt(pid: str, proj: dict) -> str:
    d = store.pdir(pid)
    return fill(store.prompt("system"),
                GLOBAL_RULES=store.read_text(store.RULES_DIR / "global_rules.md"),
                REQUEST=proj.get("request") or "(없음)",
                GUIDE=store.read_text(d / "guide.md"),
                SUMMARY=store.read_text(d / "summary.md", "(아직 없음)"))


def template_path(pid: str, proj: dict) -> Path | None:
    if proj.get("template"):
        p = store.pdir(pid) / proj["template"]
        if p.exists():
            return p
    return None


def slide_size(pid: str, proj: dict) -> tuple[float, float]:
    return tuple(proj.get("slide_size_pt") or (DEFAULT_W_PT, DEFAULT_H_PT))


def elements_doc(pid: str, proj: dict) -> str:
    w, h = slide_size(pid, proj)
    d = store.pdir(pid)
    return fill(store.prompt("_elements"), SLIDE_W=round(w), SLIDE_H=round(h),
                SLIDE_RATIO="16:9" if abs(w / h - 16 / 9) < 0.02 else f"{w / h:.2f}:1",
                USER_ASSETS=", ".join(f.name for f in _images(d / "assets")) or "(없음)",
                MAX_ASSETS=store.load_settings().get("max_assets", 6))


def log(slide: dict, role: str, text: str):
    slide["log"].append({"role": role, "text": text, "ts": time.time()})


def set_slide(pid: str, no: int, **kw):
    def fn(p):
        s = p["slides"].setdefault(str(no), store.new_slide(int(no)))
        s.update(kw)
        return s
    return store.update(pid, fn)


def current_index(sl: dict) -> int:
    if not sl["versions"]:
        return -1
    return sl["approved"] if 0 <= sl.get("approved", -1) < len(sl["versions"]) else len(sl["versions"]) - 1


def run_job(pid: str, no: int | None, label: str, fn, *args):
    """백그라운드 스레드에서 AI 작업 실행. 진행 상태는 project.json 에 기록 → UI 가 폴링."""
    def start(p):
        target = p["slides"][str(no)] if no is not None else p
        if target.get("job"):
            raise RuntimeError(f"이미 작업 중입니다: {target['job']['label']}")
        target["job"] = {"label": label, "started": time.time(), "step": ""}
        target["error"] = ""
    store.update(pid, start)

    def worker():
        _tl.pid = pid
        relay.ctx.pid, relay.ctx.no, relay.ctx.label, relay.ctx.step = pid, no, label, ""
        try:
            fn(pid, *args)
        except Exception as e:
            traceback.print_exc()

            def fail(p):
                if no is not None:
                    s = p["slides"][str(no)]
                    s["error"] = str(e)
                    if s["stage"] != "error":
                        s["stage_before_error"] = s["stage"]
                    s["stage"] = "error"
                else:
                    p["error"] = str(e)
            store.update(pid, fail)
        finally:
            def done(p):
                target = p["slides"][str(no)] if no is not None else p
                target["job"] = None
            store.update(pid, done)
            _tl.pid = None

    threading.Thread(target=worker, daemon=True).start()


def step(pid: str, no: int, text: str):
    """진행 중 세부 단계 표시 (예: '에셋 2/3 생성')."""
    relay.ctx.step = text

    def fn(p):
        j = p["slides"][str(no)].get("job")
        if j:
            j["step"] = text
    store.update(pid, fn)


# ------------------------------------------------------------ 프로젝트 단계

def do_ingest(pid: str, files: list[str]):
    d = store.pdir(pid)
    paths = [Path(f) for f in files]
    pages, note = ingest(paths, d)
    pptxs = [p for p in paths if p.suffix.lower() == ".pptx"]
    template, size = "", None
    if len(pptxs) == 1:
        shutil.copy(pptxs[0], d / "template.pptx")
        template = "template.pptx"
        try:
            from pptx import Presentation
            prs = Presentation(str(d / "template.pptx"))
            size = [prs.slide_width / 12700, prs.slide_height / 12700]
        except Exception:
            pass

    def fn(p):
        p["pages"] = pages
        p["ingest"] = {"state": "done", "message": note}
        p["template"] = template
        if size:
            p["slide_size_pt"] = size
        for pg in pages:
            s = p["slides"].setdefault(str(pg["no"]), store.new_slide(pg["no"]))
            if pg["no"] == 1 and s["stage"] == "todo":
                s["kind"] = "cover"
    store.update(pid, fn)
    do_summary(pid)


def do_summary(pid: str):
    proj = store.load(pid)
    s = store.load_settings()
    imgs = [store.pdir(pid) / pg["image"] for pg in proj["pages"] if pg["image"]][:20]
    out = llm.chat(s, system_prompt(pid, proj), [fill(store.prompt("understand"), DECK_TEXT=deck_text(proj))] + imgs)
    (store.pdir(pid) / "summary.md").write_text(out, "utf-8")


def do_guide(pid: str):
    proj = store.load(pid)
    d = store.pdir(pid)
    s = store.load_settings()
    imgs = _images(d / "refs", MAX_REFS)
    for sl in proj["slides"].values():
        i = current_index(sl)
        if sl["stage"] == "done" and i >= 0 and sl["versions"][i].get("preview"):
            imgs.append(d / sl["versions"][i]["preview"])
    out = llm.chat(s, system_prompt(pid, proj), [fill(
        store.prompt("guide_build"), GUIDE=store.read_text(d / "guide.md"),
        RULES_LOG="\n".join(f"- {r}" for r in proj.get("rules_log", [])) or "(없음)")] + imgs[:12])
    (d / "guide.md").write_text(out, "utf-8")


def add_rules(pid: str, rules: list[str], global_scope: bool = False):
    rules = [r.strip() for r in rules if r and r.strip()]
    if not rules:
        return
    d = store.pdir(pid)
    guide = store.read_text(d / "guide.md").rstrip()
    if "누적 피드백 규칙" not in guide:
        guide += "\n\n## 9. 누적 피드백 규칙"
    guide += "\n" + "\n".join(f"- {r}" for r in rules) + "\n"
    (d / "guide.md").write_text(guide, "utf-8")
    store.update(pid, lambda p: p.setdefault("rules_log", []).extend(rules))
    if global_scope:
        g = store.RULES_DIR / "global_rules.md"
        g.write_text(store.read_text(g).rstrip() + "\n" + "\n".join(f"- {r}" for r in rules) + "\n", "utf-8")


def remove_rule(pid: str, rule: str):
    d = store.pdir(pid)
    lines = store.read_text(d / "guide.md").split("\n")
    (d / "guide.md").write_text("\n".join(l for l in lines if l.strip() != f"- {rule}"), "utf-8")

    def fn(p):
        p["rules_log"] = [r for r in p.get("rules_log", []) if r != rule]
    store.update(pid, fn)


# ------------------------------------------------------------ 1. 기획

def do_plan(pid: str, no: int, feedback: str = "", annotated: str = ""):
    set_slide(pid, no, stage="planning")
    proj = store.load(pid)
    d = store.pdir(pid)
    sl = proj["slides"][str(no)]
    pg = page(proj, no)
    s = store.load_settings()
    prev = [f"[{x['no']}번]\n{x['plan'][:700]}" for x in sorted(proj["slides"].values(), key=lambda x: x["no"])
            if x["no"] != int(no) and x["stage"] not in ("todo", "planning", "plan_review") and x["plan"]][-5:]
    fb = ""
    if feedback:
        fb = f"\n--- 이전 기획안 ---\n{sl['plan']}\n\n--- 사용자 수정 요청 (반드시 반영) ---\n{feedback}"
    tpl = store.prompt("plan_cover" if sl["kind"] == "cover" else "plan")
    text = fill(tpl, SLIDE_NO=no, SLIDE_TEXT=pg["text"] or "(텍스트 없음, 이미지 참고)",
                PREV_PLANS="\n\n".join(prev) or "(없음)", FEEDBACK=fb,
                KEEP_GAME="게임/제품 이미지는 원본 그대로 둔다." if sl.get("keep_game_images") else "")
    parts = [text]
    if pg["image"]:
        parts.append(d / pg["image"])
    if annotated:
        parts.append(d / annotated)
    parts += _images(d / "refs", MAX_REFS)
    out = llm.chat(s, system_prompt(pid, proj), parts)
    warns = factcheck.check(out, deck_text(proj) + "\n" + (proj.get("request") or ""))

    def fn(p):
        x = p["slides"][str(no)]
        if x["plan"]:
            x["plan_versions"].append(x["plan"])
        x["plan"] = out
        x["warnings"] = warns
        x["stage"] = "plan_review"
        if feedback:
            log(x, "user", f"[기획 수정 요청] {feedback}")
        log(x, "ai", "기획안 작성")
    store.update(pid, fn)


# ------------------------------------------------------------ 2. 제작 (레이아웃 → 에셋 → PPTX → 미리보기)

def _gen_assets(pid: str, no: int, layout: dict):
    """레이아웃의 gen:<이름> 이미지 중 아직 없는 것만 생성."""
    s = store.load_settings()
    adir = store.slide_dir(pid, no) / "assets"
    todo = []
    for el in layout["elements"]:
        src = str(el.get("source", ""))
        if el.get("type") == "image" and src.startswith("gen:"):
            name = "".join(c for c in src[4:] if c.isalnum() or c in "-_")[:40] or el["id"]
            el["source"] = f"gen:{name}"
            if not (adir / f"{name}.png").exists() and name not in [t[0] for t in todo]:
                todo.append((name, el))
    todo = todo[: int(s.get("max_assets", 6))]
    for i, (name, el) in enumerate(todo, 1):
        step(pid, no, f"에셋 생성 {i}/{len(todo)}: {name}")
        kind = el.get("kind", "icon")
        prompt = (el.get("prompt") or name) + (
            "\nStandalone asset for a clean investor-deck slide. No text, no letters. Minimal, precise, flat or "
            "very subtle depth. No glow, no glassmorphism, no gradient background. Match brand colors in the "
            "attached style references.")
        refs = _images(store.pdir(pid) / "refs", 3)
        try:
            llm.image(s, prompt, adir / f"{name}.png", refs=refs, transparent=(kind != "background"),
                      size="1536x1024" if kind == "background" else "1024x1024")
        except relay.Skipped:
            continue  # 복붙 모드에서 건너뜀 → PPTX 에는 자리표시 박스


def _image_map(pid: str, no: int) -> dict[str, Path]:
    d = store.pdir(pid)
    m: dict[str, Path] = {}
    for f in _images(store.slide_dir(pid, no) / "assets"):
        m[f"gen:{f.stem}"] = f
    for f in _images(d / "assets"):
        m[f"user:{f.name}"] = f
    return m


def _render(pid: str, proj: dict, no: int, layout: dict, renderer: Renderer):
    d = store.pdir(pid)
    images = _image_map(pid, no)
    for el in layout.get("elements", []):
        src = str(el.get("source", ""))
        if src.startswith("file:"):
            images[src] = d / src[5:]
    pg = page(proj, no)
    src_img = d / pg["image"] if pg["image"] else None
    crop_dir = store.slide_dir(pid, no) / "crops"

    def crop(el):
        if not src_img or not src_img.exists():
            return None
        from PIL import Image
        box = el.get("src") or el
        with Image.open(src_img) as im:
            W, H = im.size
            b = (int(W * float(box["x"]) / 100), int(H * float(box["y"]) / 100),
                 int(W * (float(box["x"]) + float(box["w"])) / 100), int(H * (float(box["y"]) + float(box["h"])) / 100))
            crop_dir.mkdir(exist_ok=True)
            out = crop_dir / f"{el['id']}-{b[0]}-{b[1]}-{b[2]}-{b[3]}.png"
            if not out.exists():
                im.crop(b).save(out)
        return out

    return renderer.add_slide(layout, images, crop)


def make_version(pid: str, no: int, layout: dict, feedback: str, source: str, note: str) -> int:
    """레이아웃 → (에셋) → PPTX → 미리보기 PNG → versions 에 추가. 반환: 버전 index"""
    proj = store.load(pid)
    d = store.pdir(pid)
    layout = normalize(layout)
    if source == "ai":
        _gen_assets(pid, no, layout)
    sdir = store.slide_dir(pid, no)
    ver = len(proj["slides"][str(no)]["versions"]) + 1
    lay_f = sdir / f"v{ver}.json"
    lay_f.write_text(json.dumps(layout, ensure_ascii=False, indent=2), "utf-8")
    warns: list[str] = []
    pptx_rel, png_rel, method, resolved = "", "", "", {}
    step(pid, no, "PPTX 만드는 중")
    try:
        r = Renderer(template=template_path(pid, proj), palette=palette_from_guide(store.read_text(d / "guide.md")))
        _, resolved = _render(pid, proj, no, layout, r)
        pptx_f = r.save(sdir / f"slide-{int(no):02d}-v{ver}.pptx")
        pptx_rel = store.rel(pid, pptx_f)
        warns += r.warnings
        step(pid, no, "미리보기 렌더링 중")
        png_f = sdir / f"v{ver}.png"
        method = preview.render_png(pptx_f, png_f)
        if method:
            png_rel = store.rel(pid, png_f)
    except ImportError as e:
        warns.append(f"python-pptx 가 설치되지 않아 PPTX를 만들지 못했습니다 ({e}). 브라우저 미리보기만 표시합니다.")
        w, h = slide_size(pid, proj)
        warns += overflow_warnings(layout, w, h)
    texts = " ".join(str(e.get("text", "")) + " ".join(str(x.get("text", "")) for x in e.get("runs", []))
                     for e in layout["elements"])
    sl = proj["slides"][str(no)]
    warns = factcheck.check(texts, deck_text(proj) + "\n" + sl["plan"]) + warns
    version = {
        "layout": store.rel(pid, lay_f), "pptx": pptx_rel, "preview": png_rel, "preview_by": method,
        "images": {k: store.rel(pid, Path(v)) for k, v in resolved.items()},
        "feedback": feedback, "source": source, "note": note, "warnings": warns, "ts": time.time(),
    }

    def fn(p):
        x = p["slides"][str(no)]
        x["versions"].append(version)
        x["warnings"] = warns
        x["approved"] = -1
        return len(x["versions"]) - 1
    return store.update(pid, fn)


def load_layout(pid: str, sl: dict, idx: int | None = None) -> dict:
    i = current_index(sl) if idx is None else idx
    return json.loads((store.pdir(pid) / sl["versions"][i]["layout"]).read_text("utf-8"))


def _layout_llm(pid: str, no: int, prompt_text: str, images: list[Path]) -> dict:
    proj = store.load(pid)
    s = store.load_settings()
    res = llm.chat_json(s, system_prompt(pid, proj), [prompt_text] + [i for i in images if i and i.exists()])
    if "layout" in res and isinstance(res["layout"], dict) and "elements" not in res:
        res = {**res["layout"], "rules": res.get("rules", [])}
    return res


def do_build(pid: str, no: int):
    set_slide(pid, no, stage="building")
    proj = store.load(pid)
    d = store.pdir(pid)
    sl = proj["slides"][str(no)]
    pg = page(proj, no)
    step(pid, no, "레이아웃 설계 중")
    text = fill(store.prompt("design_layout"), PLAN=sl["plan"], SLIDE_TEXT=pg["text"],
                ELEMENTS=elements_doc(pid, proj),
                KEEP_GAME=" (이 장표는 게임/제품 이미지를 반드시 원본 그대로 유지)" if sl.get("keep_game_images") else "")
    imgs = ([d / pg["image"]] if pg["image"] else []) + _images(d / "refs", MAX_REFS) + _images(d / "assets", 6)
    layout = _layout_llm(pid, no, text, imgs)
    idx = make_version(pid, no, layout, "", "ai", "첫 제작")
    idx = _self_check(pid, no, idx)
    _to_review(pid, no, "장표 제작 완료")


def _self_check(pid: str, no: int, idx: int) -> int:
    """사용자에게 보여주기 전 AI 자체 점검 → 문제 있으면 1회 자동 수정."""
    s = store.load_settings()
    if not s.get("self_check", True) or (s.get("mode") == "manual" and not s.get("mock")):
        return idx  # 복붙 모드에서는 왕복 횟수를 줄이려고 자체 점검 생략
    proj = store.load(pid)
    d = store.pdir(pid)
    sl = proj["slides"][str(no)]
    v = sl["versions"][idx]
    auto = [w for w in v["warnings"] if "넘칠" in w or "팔레트" in w or "원본에 없는" in w]
    issues = list(auto)
    if v.get("preview"):
        step(pid, no, "AI 자체 점검 중")
        res = _layout_llm(pid, no, fill(store.prompt("selfcheck"), AUTO="\n".join(auto) or "(없음)"),
                          [d / v["preview"]])
        issues += [str(i) for i in res.get("issues", [])]
    if not issues:
        return idx
    step(pid, no, f"자체 점검 문제 {len(issues)}개 수정 중")
    return _revise_core(pid, no, idx, "AI 자체 점검에서 발견한 문제:\n- " + "\n- ".join(issues), [], "",
                        source="ai", note="AI 자체 점검 수정")


def _to_review(pid: str, no: int, msg: str):
    def fn(p):
        x = p["slides"][str(no)]
        x["stage"] = "review"
        log(x, "ai", msg)
    store.update(pid, fn)


# ------------------------------------------------------------ 3. 수정

def marked_elements(layout: dict, marks: list[dict]) -> str:
    """빨간 펜 영역(퍼센트 bbox)과 겹치는 요소 찾기."""
    if not marks:
        return "(없음)"
    out = []
    for i, m in enumerate(marks, 1):
        mx1, my1, mx2, my2 = m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"]
        hit = []
        for el in layout.get("elements", []):
            if el.get("type") == "line":
                ex1, ex2 = sorted((el.get("x1", 0), el.get("x2", 0)))
                ey1, ey2 = sorted((el.get("y1", 0), el.get("y2", 0)))
            else:
                ex1, ey1 = el.get("x", 0), el.get("y", 0)
                ex2, ey2 = ex1 + el.get("w", 0), ey1 + el.get("h", 0)
            if ex1 <= mx2 and ex2 >= mx1 and ey1 <= my2 and ey2 >= my1:
                label = str(el.get("text") or "".join(r.get("text", "") for r in el.get("runs", [])) or el["type"])
                hit.append(f"{el['id']}({el['type']}: {label[:20]})")
        out.append(f"표시{i} 영역 x{m['x']:.0f}~{mx2:.0f}% y{m['y']:.0f}~{my2:.0f}%"
                   + (f" 메모 '{m['note']}'" if m.get("note") else "") + f" → {', '.join(hit) or '빈 공간'}")
    return "\n".join(out)


def _revise_core(pid: str, no: int, base_idx: int, feedback: str, marks: list, annotated: str,
                 source: str = "ai", note: str = "피드백 수정", save_rule: str = "") -> int:
    proj = store.load(pid)
    d = store.pdir(pid)
    sl = proj["slides"][str(no)]
    base = load_layout(pid, sl, base_idx)
    v = sl["versions"][base_idx]
    pg = page(proj, no)
    step(pid, no, "레이아웃 수정 중")
    text = fill(store.prompt("revise"), MARKED=marked_elements(base, marks), ELEMENTS=elements_doc(pid, proj),
                FEEDBACK=feedback, LAYOUT=json.dumps(base, ensure_ascii=False), PLAN=sl["plan"],
                RULES_ASK=RULES_ASK if save_rule else "")
    shot = d / annotated if annotated else (d / v["preview"] if v.get("preview") else None)
    layout = _layout_llm(pid, no, text, [shot, d / pg["image"] if pg["image"] else None])
    rules = [str(r) for r in (layout.pop("rules", None) or []) if str(r).strip()]
    if save_rule and rules:
        add_rules(pid, rules, global_scope=(save_rule == "global"))
    return make_version(pid, no, layout, feedback, source, note)


RULES_ASK = ('\n추가로: 이 피드백 중 "앞으로 모든 장표에 계속 적용할 디자인 규칙"을 최상위 "rules" 배열에 '
             '짧은 명령형 한 줄씩 넣어줘 (이번 장표에만 해당하는 수정은 제외, 없으면 빈 배열).')


def do_revise(pid: str, no: int, feedback: str, marks: list, annotated: str, save_rule: str, base_idx: int):
    set_slide(pid, no, stage="revising")
    set_slide_log(pid, no, "user", f"[수정 요청] {feedback}" + (f" (빨간 펜 {len(marks)}곳)" if marks else ""))
    _revise_core(pid, no, base_idx, feedback, marks, annotated, save_rule=save_rule if feedback else "")
    _to_review(pid, no, "수정본 완성")


def set_slide_log(pid, no, role, text):
    store.update(pid, lambda p: log(p["slides"][str(no)], role, text))


def do_import(pid: str, no: int, pptx_file: str):
    """사용자가 PowerPoint 에서 고친 파일 → 새 버전 (이후 AI 수정은 이걸 기준으로)."""
    from .importer import import_slide
    set_slide(pid, no, stage="revising")
    sdir = store.slide_dir(pid, no)
    media = sdir / "media"
    layout, warns = import_slide(store.pdir(pid) / pptx_file, media, store.rel(pid, media))
    make_version(pid, no, layout, "", "manual", "내가 PowerPoint에서 직접 수정")
    if warns:
        store.update(pid, lambda p: p["slides"][str(no)]["warnings"].extend(warns))
    _to_review(pid, no, "직접 수정한 PPTX를 불러왔습니다")


# ------------------------------------------------------------ 채팅 (동기)

def chat(pid: str, no: int, message: str) -> str:
    _tl.pid = pid
    relay.ctx.pid, relay.ctx.no, relay.ctx.label, relay.ctx.step = pid, no, "채팅 답변", ""
    try:
        proj = store.load(pid)
        d = store.pdir(pid)
        sl = proj["slides"][str(no)]
        s = store.load_settings()
        idx = current_index(sl)
        layout_txt, imgs = "(아직 없음)", []
        if idx >= 0:
            lay = load_layout(pid, sl, idx)
            layout_txt = "\n".join(
                f"{e['id']} {e['type']} ({e.get('x', e.get('x1', 0)):.0f},{e.get('y', e.get('y1', 0)):.0f}) "
                f"{(e.get('text') or ''.join(r.get('text', '') for r in e.get('runs', [])))[:40]}"
                f" {e.get('size', '')}pt {e.get('color', e.get('fill', ''))}" for e in lay["elements"])
            if sl["versions"][idx].get("preview"):
                imgs.append(d / sl["versions"][idx]["preview"])
        pg = page(proj, no)
        if pg["image"]:
            imgs.append(d / pg["image"])
        text = fill(store.prompt("chat"), SLIDE_NO=no, PLAN=sl["plan"] or "(없음)", LAYOUT=layout_txt, MESSAGE=message)
        answer = llm.chat(s, system_prompt(pid, proj), [text] + imgs, history=sl["chat"][-12:])

        def fn(p):
            c = p["slides"][str(no)]["chat"]
            c.append({"role": "user", "text": message, "ts": time.time()})
            c.append({"role": "ai", "text": answer, "ts": time.time()})
        store.update(pid, fn)
        return answer
    finally:
        _tl.pid = None


# ------------------------------------------------------------ 사용자 액션

def act(pid: str, no: int, action: str, feedback: str = "", annotated: str = "", save_rule: str = "",
        marks: list | None = None, version: int | None = None, upload: str = ""):
    proj = store.load(pid)
    sl = proj["slides"].get(str(no))
    if sl is None:
        raise ValueError("장표 없음")
    idx = version if version is not None and 0 <= version < len(sl["versions"]) else current_index(sl)
    if action == "plan":
        run_job(pid, no, "기획안 작성", do_plan, no, feedback, annotated)
    elif action in ("approve_plan", "rebuild"):
        if not sl["plan"]:
            raise ValueError("기획안이 없습니다")
        run_job(pid, no, "장표 제작", do_build, no)
    elif action == "revise":
        if idx < 0:
            raise ValueError("수정할 장표가 없습니다")
        if not feedback and not marks and not annotated:
            raise ValueError("피드백을 적거나 빨간 펜으로 표시해 주세요")
        run_job(pid, no, "수정", do_revise, no, feedback or "빨간 펜으로 표시한 부분을 수정", marks or [],
                annotated, save_rule, idx)
    elif action == "import":
        if not upload:
            raise ValueError("PPTX 파일을 올려주세요")
        run_job(pid, no, "직접 수정본 불러오기", do_import, no, upload)
    elif action == "approve":
        if idx < 0:
            raise ValueError("확정할 장표가 없습니다")

        def fn(p):
            x = p["slides"][str(no)]
            x["approved"] = idx
            x["stage"] = "done"
            log(x, "user", f"v{idx + 1} 확정")
        store.update(pid, fn)
    elif action == "reopen":
        set_slide(pid, no, stage="review")
    elif action == "retry":
        back = sl.get("stage_before_error") or "todo"
        if back in ("planning", "todo"):
            run_job(pid, no, "기획안 작성", do_plan, no)
        elif back == "building" or (back == "revising" and not sl["versions"]):
            run_job(pid, no, "장표 제작", do_build, no)
        else:
            set_slide(pid, no, stage="review" if sl["versions"] else "plan_review", error="")
    elif action == "reset":
        set_slide(pid, no, stage="todo", error="")
    else:
        raise ValueError(f"알 수 없는 액션: {action}")


# ------------------------------------------------------------ 내보내기

def assets_zip(pid: str, no: int) -> bytes:
    proj = store.load(pid)
    d = store.pdir(pid)
    sl = proj["slides"][str(no)]
    idx = current_index(sl)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        prompts = []
        if idx >= 0:
            lay = load_layout(pid, sl, idx)
            imap = _image_map(pid, no)
            for el in lay["elements"]:
                src = str(el.get("source", ""))
                if src.startswith("gen:") and src in imap:
                    kind = el.get("kind", "icon")
                    z.write(imap[src], f"{kind}/{imap[src].name}")
                    prompts.append(f"[{src[4:]}] ({kind})\n{el.get('prompt', '')}")
            for rel in sl["versions"][idx].get("images", {}).values():
                f = d / rel
                if "crops" in rel and f.exists():
                    z.write(f, f"원본에서-잘라낸-이미지/{f.name}")
        z.writestr("prompts.txt", "\n\n".join(prompts) or "(이 장표에서 생성한 에셋 없음)")
    return buf.getvalue()


def slide_pptx(pid: str, no: int) -> Path:
    sl = store.load(pid)["slides"][str(no)]
    idx = current_index(sl)
    if idx < 0 or not sl["versions"][idx]["pptx"]:
        raise ValueError("PPTX 없음")
    return store.pdir(pid) / sl["versions"][idx]["pptx"]


def build_deck(pid: str, include_review: bool = False) -> Path:
    """원본 PPTX 가 있으면 그 파일(테마/마스터/나머지 장표 유지)에 완료 장표만 교체."""
    proj = store.load(pid)
    d = store.pdir(pid)
    tpl = template_path(pid, proj)
    r = Renderer(template=tpl, keep_slides=bool(tpl), palette=palette_from_guide(store.read_text(d / "guide.md")))
    new = {}
    for sl in sorted(proj["slides"].values(), key=lambda x: x["no"]):
        idx = current_index(sl)
        if idx < 0 or not (sl["stage"] == "done" or include_review):
            continue
        _render(pid, proj, sl["no"], load_layout(pid, sl, idx), r)
        new[sl["no"]] = r.last_sid
    if not new:
        raise ValueError("완료된 장표가 없습니다")
    if tpl:
        r.replace_slides(new)
    return r.save(d / "deck.pptx")


def recover_stale():
    """프로그램 재시작 시: 중간에 끊긴 작업을 '오류(다시 시도 가능)' 상태로 바꾼다."""
    for f in store.PROJECTS_DIR.glob("*/project.json"):
        pid = f.parent.name

        def fn(p):
            if p.get("job"):
                p["job"] = None
                p["error"] = "프로그램이 재시작되어 작업이 중단됐어요. 필요하면 다시 실행해 주세요."
            for s in p["slides"].values():
                if s.get("job"):
                    s["job"] = None
                    if s["stage"] != "error":
                        s["stage_before_error"] = s["stage"]
                    s["stage"] = "error"
                    s["error"] = "프로그램이 재시작되어 작업이 중단됐어요. [다시 시도]를 눌러주세요."
        try:
            store.update(pid, fn)
        except Exception:
            pass
