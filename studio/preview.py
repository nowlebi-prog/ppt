"""PPTX → PNG 미리보기 (실제 PowerPoint/LibreOffice 렌더링 = 받게 될 파일 그대로).

없으면 브라우저 미리보기(레이아웃 JSON 기반, 근사치)만 사용한다.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_lock = threading.Lock()   # PowerPoint 는 동시에 여러 번 호출하면 불안정
_broken: set[str] = set()   # 한 번 실패한 방식은 다시 시도하지 않음 (속도)


def _soffice() -> str | None:
    for exe in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if shutil.which(exe) or Path(exe).exists():
            return exe
    return None


def available() -> str:
    if platform.system() == "Windows" and "powerpoint" not in _broken:
        return "powerpoint"
    if _soffice() and "libreoffice" not in _broken:
        return "libreoffice"
    return ""


def _powerpoint(pptx: Path, out: Path) -> bool:
    ps = (
        "$ErrorActionPreference='Stop';"
        "$app=New-Object -ComObject PowerPoint.Application;"
        f"$p=$app.Presentations.Open('{pptx.resolve()}',-1,0,0);"
        "$w=1600;$h=[int](1600*$p.PageSetup.SlideHeight/$p.PageSetup.SlideWidth);"
        f"$p.Slides.Item(1).Export('{out.resolve()}','PNG',$w,$h);$p.Close();"
        "if($app.Presentations.Count -eq 0){$app.Quit()}"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=120, capture_output=True)
    return r.returncode == 0 and out.exists()


def _libreoffice(pptx: Path, out: Path) -> bool:
    exe = _soffice()
    if not exe:
        return False
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(pptx)],
                       timeout=180, capture_output=True)
        pdf = Path(td) / (pptx.stem + ".pdf")
        if not pdf.exists():
            return False
        import fitz
        doc = fitz.open(str(pdf))
        page = doc[0]
        zoom = 1600 / max(page.rect.width, 1)
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).save(str(out))
    return out.exists()


def render_png(pptx: Path, out: Path) -> str:
    """성공 시 사용한 방식 이름, 실패 시 ''."""
    with _lock:
        for name, fn in (("powerpoint", _powerpoint), ("libreoffice", _libreoffice)):
            if name in _broken:
                continue
            if name == "powerpoint" and platform.system() != "Windows":
                continue
            try:
                if fn(pptx, out):
                    return name
            except Exception:
                pass
            _broken.add(name)
    return ""
