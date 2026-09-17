"""Dependency-free text normalization shared by capture and derived builders."""
from __future__ import annotations

import re
from typing import Any

def clean(value: Any) -> str:
    # Repair BEFORE collapsing whitespace: mojibake often contains NBSP
    # (\xa0) and NEL (\x85), which count as Unicode whitespace, so collapsing
    # first would destroy the byte sequence and make the text unrecoverable.
    return re.sub(r"\s+", " ", repair_mojibake(str(value or ""))).strip()


def repair_mojibake(text: str) -> str:
    if not text or not looks_like_mojibake(text):
        return text
    try:
        decoded = text.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return _repair_space_damaged_mojibake(text)
    if "\ufffd" in decoded:
        return text
    return decoded if mojibake_score(decoded) < mojibake_score(text) else text


def _repair_space_damaged_mojibake(text: str) -> str:
    """Salvage mojibake whose whitespace bytes were collapsed to plain spaces
    by an earlier pipeline run (e.g. "\u00e5 \u00a8\u00e5\u00bd" for \u5168\u56fd, where NEL \\x85 became
    a space). Spaces adjacent to high latin-1 bytes are re-tried as \\x85/\\xa0."""
    try:
        raw = bytearray(text.encode("latin-1"))
    except UnicodeError:
        return text
    suspects = [
        i for i, b in enumerate(raw)
        if b == 0x20
        and ((i > 0 and raw[i - 1] >= 0x80) or (i + 1 < len(raw) and raw[i + 1] >= 0x80))
    ]
    if not suspects or len(suspects) > 8:  # cap the search space
        return text
    best = None
    for mask in range(3 ** len(suspects)):
        candidate = bytearray(raw)
        m = mask
        for pos in suspects:
            choice = m % 3
            m //= 3
            if choice == 1:
                candidate[pos] = 0x85
            elif choice == 2:
                candidate[pos] = 0xA0
        try:
            decoded = bytes(candidate).decode("utf-8")
        except UnicodeError:
            continue
        if "\ufffd" in decoded:
            continue
        score = mojibake_score(decoded)
        if score >= mojibake_score(text):
            continue
        # Ambiguous bytes (e.g. \x85 vs \xa0 both decode) are resolved by
        # preferring decodings with more domain-common CJK characters.
        rank = (score - sum(2 for ch in decoded if ch in _COMMON_CJK), decoded)
        if best is None or rank < best:
            best = rank
    return best[1] if best else text


_COMMON_CJK = (
    "年全国国际象棋公开赛站锦标青少联乙甲级杯组个人团体届冠军中协大师"
    "省市区学校俱乐部锦标赛春夏秋冬季月日第轮"
)


def looks_like_mojibake(text: str) -> bool:
    return bool(re.search(r"[ÃÂâåæèéäï]|[\u0080-\u009f]", text))


def mojibake_score(text: str) -> int:
    marker_count = len(re.findall(r"[ÃÂâåæèéäï]|[\u0080-\u009f]", text))
    replacement_count = text.count("\ufffd")
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return marker_count * 3 + replacement_count * 20 - cjk_count


