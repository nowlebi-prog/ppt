"""여러 이미지를 번호가 붙은 '모음 시트' 몇 장으로 합친다 (복붙 모드에서 첨부 수를 줄이기 위함)."""
from __future__ import annotations

import hashlib
from pathlib import Path

TILE_W, TILE_H, PAD, HEAD = 640, 360, 16, 44


def _font(size: int):
    from PIL import ImageFont
    for name in ("malgunbd.ttf", "malgun.ttf", "AppleSDGothicNeo.ttc", "NotoSansCJK-Bold.ttc", "arialbd.ttf",
                 "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def contact_sheets(items: list[tuple[Path, str]], out_dir: Path, per_sheet: int = 9, cols: int = 3) -> list[Path]:
    """items: [(이미지 경로, 타일 라벨)]. 반환: 시트 경로들 (캐시됨)."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for start in range(0, len(items), per_sheet):
        chunk = items[start:start + per_sheet]
        key = hashlib.sha1("|".join(f"{p}:{p.stat().st_mtime if p.exists() else 0}:{lab}"
                                    for p, lab in chunk).encode()).hexdigest()[:16]
        out = out_dir / f"sheet-{key}.png"
        if not out.exists():
            n = len(chunk)
            c = min(cols, n)
            rows = (n + c - 1) // c
            W = c * TILE_W + (c + 1) * PAD
            H = rows * (TILE_H + HEAD) + (rows + 1) * PAD
            sheet = Image.new("RGB", (W, H), (255, 255, 255))
            draw = ImageDraw.Draw(sheet)
            font = _font(26)
            for i, (p, label) in enumerate(chunk):
                x = PAD + (i % c) * (TILE_W + PAD)
                y = PAD + (i // c) * (TILE_H + HEAD + PAD)
                draw.rectangle([x, y, x + TILE_W, y + HEAD - 6], fill=(17, 17, 17))
                draw.text((x + 12, y + 6), label, fill=(255, 255, 255), font=font)
                try:
                    with Image.open(p) as im:
                        im = im.convert("RGB")
                        im.thumbnail((TILE_W, TILE_H))
                        ox = x + (TILE_W - im.width) // 2
                        oy = y + HEAD + (TILE_H - im.height) // 2
                        draw.rectangle([x, y + HEAD, x + TILE_W, y + HEAD + TILE_H], outline=(220, 220, 216))
                        sheet.paste(im, (ox, oy))
                except Exception:
                    draw.text((x + 12, y + HEAD + 12), "(열 수 없음)", fill=(116, 116, 116), font=font)
            sheet.save(out, optimize=True)
        sheets.append(out)
    return sheets
