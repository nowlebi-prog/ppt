"""원본에 없는 숫자/표현 감지 ("지어낸 얘기 금지" 규칙 자동 검사)."""
from __future__ import annotations

import re

NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|억|천)?")
UNIT = {"천": 1_000, "만": 10_000, "억": 100_000_000}

# 원본에 없으면 경고할 '해석성' 표현 (프로젝트 가이드에서 추가 가능)
DEFAULT_BANNED = ["낮은 CAC", "효율적인 UA", "지속 가능한 성장", "바이럴 성장", "압도적", "업계 최초", "폭발적"]


def numbers(text: str) -> set[float]:
    out = set()
    for m in NUM_RE.finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out.add(v)
        if m.group(2):
            out.add(v * UNIT[m.group(2)])
    return out


def _known(v: float, src: set[float]) -> bool:
    if v <= 12:  # 번호/단계(1~12) 등 작은 숫자는 무시
        return True
    for s in src:
        if s == v or (s and abs(s - v) / s < 0.01):  # 반올림(24.9만 ≈ 249,106) 허용
            return True
    return False


def check(output: str, source: str, banned: list[str] | None = None) -> list[str]:
    src = numbers(source)
    warns = []
    seen = set()
    for m in NUM_RE.finditer(output or ""):
        raw = m.group(0).strip()
        v = float(m.group(1).replace(",", "")) * UNIT.get(m.group(2) or "", 1)
        base = float(m.group(1).replace(",", ""))
        if raw in seen or _known(v, src) or _known(base, src):
            continue
        seen.add(raw)
        warns.append(f"원본에 없는 숫자: {raw}")
    for b in (banned or DEFAULT_BANNED):
        if b and b in (output or "") and b not in (source or ""):
            warns.append(f"원본에 없는 해석성 표현: '{b}'")
    return warns
