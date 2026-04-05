#!/usr/bin/env python3
"""
Quantum I Ching Oracle — Yarrow-Stalk Method
=============================================
Uses the ANU Quantum Random Number Generator for genuine quantum randomness,
with a cryptographically secure OS-entropy fallback.

Usage:
     % echo 'export ANU_QRNG_API_KEY="KEY"' >> ~/.zshrc
source ~/.zshrc

    python iching_oracle.py              # normal oracle mode
    
    python iching_oracle.py --debug 50000  # run 50000 local consultations, print stats
"""

import http.client
import json
import os
import random
import secrets
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

API_KEY: str = os.environ.get("ANU_QRNG_API_KEY", "")
API_HOST: str = "api.quantumnumbers.anu.edu.au"
API_TIMEOUT: int = 10          # seconds per request
MAX_RETRIES: int = 3
RETRY_DELAY: float = 1.0       # seconds; multiplied by attempt number

# ═══════════════════════════════════════════════════════════════════
# I Ching Data — Trigrams
# ═══════════════════════════════════════════════════════════════════
#
# Binary keys represent lines BOTTOM→TOP: "1"=yang, "0"=yin.
#
#   Ch'ien  111  7      K'un  000  0
#   Chen    100  4      Sun   110  6   ← Sun is yang-yang-yin (bottom→top)
#   K'an    010  2      Ken   001  1   ← Ken is yin-yin-yang (bottom→top)
#   Li      101  5      Tui   011  3
#
# BUG FIXED: the original code had Sun ("110") and Ken ("001") swapped.
# Mountain (Ken) has one yang line at the TOP → yin, yin, yang → "001".
# Wind/Wood (Sun) has one yin line at the BOTTOM → yang, yang, yin → "110".

TRIGRAMS: Dict[str, Dict] = {
    "111": {"symbol": "☰", "name": "Ch'ien", "meaning": "Heaven",   "int": 7},
    "000": {"symbol": "☷", "name": "K'un",   "meaning": "Earth",    "int": 0},
    "100": {"symbol": "☳", "name": "Chen",   "meaning": "Thunder",  "int": 4},
    "010": {"symbol": "☵", "name": "K'an",   "meaning": "Water",    "int": 2},
    "110": {"symbol": "☴", "name": "Sun",    "meaning": "Wind",     "int": 6},  # Fixed
    "001": {"symbol": "☶", "name": "Ken",    "meaning": "Mountain", "int": 1},  # Fixed
    "101": {"symbol": "☲", "name": "Li",     "meaning": "Fire",     "int": 5},
    "011": {"symbol": "☱", "name": "Tui",    "meaning": "Lake",     "int": 3},
}

# ═══════════════════════════════════════════════════════════════════
# I Ching Data — King Wen Lookup Table
# ═══════════════════════════════════════════════════════════════════
# The correct approach is a (lower_int, upper_int) → hexagram_number lookup.
# Trigram ints follow the binary keys above:
#   Ch'ien=7  Chen=4  K'an=2  Ken=1  K'un=0  Sun=6  Li=5  Tui=3
#
# Verified against the standard Wilhelm/Baynes table.

KING_WEN_LOOKUP: Dict[Tuple[int, int], int] = {
    # ── lower Ch'ien (7) ──────────────────────────────────────────
    (7, 7): 1,  (7, 4): 34, (7, 2): 5,  (7, 1): 26,
    (7, 0): 11, (7, 6): 9,  (7, 5): 14, (7, 3): 43,
    # ── lower Chen (4) ────────────────────────────────────────────
    (4, 7): 25, (4, 4): 51, (4, 2): 3,  (4, 1): 27,
    (4, 0): 24, (4, 6): 42, (4, 5): 21, (4, 3): 17,
    # ── lower K'an (2) ────────────────────────────────────────────
    (2, 7): 6,  (2, 4): 40, (2, 2): 29, (2, 1): 4,
    (2, 0): 7,  (2, 6): 59, (2, 5): 64, (2, 3): 47,
    # ── lower Ken (1) ─────────────────────────────────────────────
    (1, 7): 33, (1, 4): 62, (1, 2): 39, (1, 1): 52,
    (1, 0): 15, (1, 6): 53, (1, 5): 56, (1, 3): 31,
    # ── lower K'un (0) ────────────────────────────────────────────
    (0, 7): 12, (0, 4): 16, (0, 2): 8,  (0, 1): 23,
    (0, 0): 2,  (0, 6): 20, (0, 5): 35, (0, 3): 45,
    # ── lower Sun (6) ─────────────────────────────────────────────
    (6, 7): 9,  (6, 4): 32, (6, 2): 48, (6, 1): 18,
    (6, 0): 46, (6, 6): 57, (6, 5): 50, (6, 3): 28,
    # ── lower Li (5) ──────────────────────────────────────────────
    (5, 7): 13, (5, 4): 55, (5, 2): 63, (5, 1): 22,
    (5, 0): 36, (5, 6): 37, (5, 5): 30, (5, 3): 49,
    # ── lower Tui (3) ─────────────────────────────────────────────
    (3, 7): 10, (3, 4): 54, (3, 2): 60, (3, 1): 41,
    (3, 0): 19, (3, 6): 61, (3, 5): 38, (3, 3): 58,
}

assert len(KING_WEN_LOOKUP) == 64, "Lookup table must have exactly 64 entries."

# 64 hexagram names in King Wen order; index 0 → hexagram 1.
HEXAGRAM_NAMES: List[str] = [
    "Ch'ien (The Creative)",            "K'un (The Receptive)",
    "Chun (Difficulty at the Beginning)","Mêng (Youthful Folly)",
    "Hsü (Waiting)",                    "Sung (Conflict)",
    "Shih (The Army)",                  "Pi (Holding Together)",
    "Hsiao Ch'u (Small Taming)",        "Lü (Treading)",
    "T'ai (Peace)",                     "P'i (Standstill)",
    "T'ung Jên (Fellowship)",           "Ta Yu (Great Possession)",
    "Ch'ien (Modesty)",                 "Yü (Enthusiasm)",
    "Sui (Following)",                  "Ku (Work on the Decayed)",
    "Lin (Approach)",                   "Kuan (Contemplation)",
    "Shih Ho (Biting Through)",         "Pi (Grace)",
    "Po (Splitting Apart)",             "Fu (Return)",
    "Wu Wang (Innocence)",              "Ta Ch'u (Great Taming)",
    "I (Nourishment)",                  "Ta Kuo (Great Excess)",
    "K'an (The Abyss)",                 "Li (The Clinging)",
    "Hsien (Influence)",                "Hêng (Duration)",
    "Tun (Retreat)",                    "Ta Chuang (Great Power)",
    "Chin (Progress)",                  "Ming I (Darkening of the Light)",
    "Chia Jên (The Family)",            "K'uei (Opposition)",
    "Chien (Obstruction)",              "Hsieh (Deliverance)",
    "Sun (Decrease)",                   "I (Increase)",
    "Kuai (Breakthrough)",              "Kou (Coming to Meet)",
    "Ts'ui (Gathering Together)",       "Shêng (Pushing Upward)",
    "K'un (Oppression)",                "Ching (The Well)",
    "Ko (Revolution)",                  "Ting (The Cauldron)",
    "Chên (The Arousing)",              "Kên (Keeping Still)",
    "Chien (Development)",              "Kuei Mei (The Marrying Maiden)",
    "Fêng (Abundance)",                 "Lü (The Wanderer)",
    "Sun (The Gentle)",                 "Tui (The Joyous)",
    "Huan (Dispersion)",                "Chieh (Limitation)",
    "Chung Fu (Inner Truth)",           "Hsiao Kuo (Small Excess)",
    "Chi Chi (After Completion)",       "Wei Chi (Before Completion)",
]

LINE_POSITIONS: List[Dict] = [
    {"position": 1, "significance": "Beginning",    "realm": "Initial forces"},
    {"position": 2, "significance": "Inner",        "realm": "Inner world"},
    {"position": 3, "significance": "Transitional", "realm": "Breaking point"},
    {"position": 4, "significance": "Outer",        "realm": "External influence"},
    {"position": 5, "significance": "Influence",    "realm": "Power position"},
    {"position": 6, "significance": "Culmination",  "realm": "Final outcome"},
]

# ═══════════════════════════════════════════════════════════════════
# Domain Model
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Line:
    """A single I Ching line with its type and change status."""
    yang: bool          # True = yang (—), False = yin (- -)
    changing: bool      # True = "old" line, will transform
    line_type: str      # 'old_yang' | 'young_yang' | 'old_yin' | 'young_yin'

    @property
    def symbol(self) -> str:
        return "———" if self.yang else "- -"

    def flipped(self) -> "Line":
        """Return the transformed version of this line (yang↔yin)."""
        new_type = "young_yin" if self.yang else "young_yang"
        return Line(yang=not self.yang, changing=False, line_type=new_type)


@dataclass
class Hexagram:
    """
    Six I Ching lines.  lines[0] = line 1 (bottom), lines[5] = line 6 (top).
    The trigram binary strings are read bottom→top so that line 1 is the
    least-significant position, consistent with the TRIGRAMS table.
    """
    lines: List[Line]

    # ── Trigram helpers ───────────────────────────────────────────

    def _trigram_binary(self, line_slice: List[Line]) -> str:
        return "".join("1" if l.yang else "0" for l in line_slice)

    def lower_trigram(self) -> Dict:
        return TRIGRAMS[self._trigram_binary(self.lines[:3])]

    def upper_trigram(self) -> Dict:
        return TRIGRAMS[self._trigram_binary(self.lines[3:])]

    def trigrams(self) -> Dict[str, Dict]:
        return {"lower": self.lower_trigram(), "upper": self.upper_trigram()}

    # ── Identification ────────────────────────────────────────────

    def number(self) -> int:
        """Return the King Wen sequence number (1–64)."""
        lower_int = self.lower_trigram()["int"]
        upper_int = self.upper_trigram()["int"]
        return KING_WEN_LOOKUP[(lower_int, upper_int)]

    def name(self) -> str:
        """Return the full name string, e.g. '1. Ch'ien (The Creative)'."""
        n = self.number()
        return f"{n}. {HEXAGRAM_NAMES[n - 1]}"

    # ── Derived hexagrams ─────────────────────────────────────────

    def nuclear(self) -> "Hexagram":
        """
        Nuclear hexagram: lines 2–4 form the lower nuclear trigram,
        lines 3–5 form the upper nuclear trigram.
        (indices 1–3 and 2–4 in zero-based list)
        """
        return Hexagram(self.lines[1:4] + self.lines[2:5])

    def changed(self) -> "Hexagram":
        """Return the future hexagram with all changing lines flipped."""
        return Hexagram([l.flipped() if l.changing else l for l in self.lines])

    # ── Convenience ───────────────────────────────────────────────

    @property
    def changing_line_numbers(self) -> List[int]:
        """1-based list of positions that are changing."""
        return [i + 1 for i, l in enumerate(self.lines) if l.changing]

    @property
    def has_changing_lines(self) -> bool:
        return bool(self.changing_line_numbers)

    def display_lines(self, *, mark_changing: bool = True) -> List[str]:
        rows = []
        for i, line in enumerate(self.lines):
            mark = "  ← changing" if (mark_changing and line.changing) else ""
            rows.append(f"  Line {i + 1}:  {line.symbol}{mark}")
        return rows


@dataclass
class Reading:
    timestamp: datetime
    hexagram: Hexagram
    rng_source: str   # "quantum" | "csprng" | "local"


# ═══════════════════════════════════════════════════════════════════
# Session Statistics
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SessionStats:
    consultations: int = 0
    api_calls: int = 0
    api_errors: int = 0
    numbers_requested: int = 0
    numbers_received: int = 0
    csprng_fallbacks: int = 0
    changing_lines_per_reading: List[int] = field(default_factory=list)
    line_type_counts: Counter = field(default_factory=Counter)
    api_response_times: List[float] = field(default_factory=list)
    hexagram_frequency: Dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    trigram_frequency: Dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    failed_requests: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    def record_reading(self, reading: Reading) -> None:
        self.consultations += 1
        hex_ = reading.hexagram
        self.changing_lines_per_reading.append(len(hex_.changing_line_numbers))
        self.hexagram_frequency[hex_.name()] += 1
        self.hexagram_frequency[hex_.nuclear().name()] += 1
        if hex_.has_changing_lines:
            self.hexagram_frequency[hex_.changed().name()] += 1
        for trigram in hex_.trigrams().values():
            self.trigram_frequency[trigram["symbol"]] += 1

    def summary(self) -> str:
        duration = (
            (self.end_time - self.start_time).total_seconds()
            if self.end_time and self.start_time else 0
        )
        total_lines = sum(self.line_type_counts.values())

        def pct(n: int, denom: int = total_lines) -> str:
            return f"{n / denom * 100:.2f}%" if denom else "—"

        avg_changing = (
            sum(self.changing_lines_per_reading) / len(self.changing_lines_per_reading)
            if self.changing_lines_per_reading else 0
        )
        avg_response = (
            sum(self.api_response_times) / len(self.api_response_times)
            if self.api_response_times else 0
        )

        lines = [
            "",
            "╔═══════════════════════════════════════════╗",
            "║   QUANTUM I CHING — SESSION STATISTICS    ║",
            "╚═══════════════════════════════════════════╝",
            f"  Duration:            {duration:.1f}s",
            f"  Consultations:       {self.consultations}",
            f"  Quantum draws:       {self.numbers_received}",
            f"  CSPRNG fallbacks:    {self.csprng_fallbacks}",
            "",
            "  API Performance",
            f"    Calls:             {self.api_calls}",
            f"    Errors:            {self.api_errors}",
            f"    Avg response time: {avg_response:.3f}s",
            "",
            "  Line Distribution — Yarrow-Stalk Method",
            "  (Expected probabilities from the traditional procedure)",
            f"    Old Yang  (9) — exp 18.75%:  {self.line_type_counts['old_yang']:4d}  got {pct(self.line_type_counts['old_yang'])}",
            f"    Young Yang(7) — exp 31.25%:  {self.line_type_counts['young_yang']:4d}  got {pct(self.line_type_counts['young_yang'])}",
            f"    Young Yin (8) — exp 43.75%:  {self.line_type_counts['young_yin']:4d}  got {pct(self.line_type_counts['young_yin'])}",
            f"    Old Yin   (6) — exp  6.25%:  {self.line_type_counts['old_yin']:4d}  got {pct(self.line_type_counts['old_yin'])}",
            "",
            f"  Avg changing lines per reading: {avg_changing:.2f}",
            f"  Changing line distribution:     {dict(Counter(self.changing_lines_per_reading))}",
        ]

        if self.hexagram_frequency:
            top = sorted(
                self.hexagram_frequency.items(), key=lambda x: x[1], reverse=True
            )[:5]
            lines += ["", "  Top 5 Hexagrams"]
            for name, count in top:
                lines.append(f"    {count:3d}×  {name}")

        if self.trigram_frequency:
            top_t = sorted(
                self.trigram_frequency.items(), key=lambda x: x[1], reverse=True
            )[:5]
            lines += ["", "  Top 5 Trigrams"]
            for sym, count in top_t:
                lines.append(f"    {sym}  {count}×")

        if self.failed_requests:
            lines += ["", "  Recent API Errors (last 5)"]
            for err in self.failed_requests[-5:]:
                lines.append(f"    {err}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Quantum RNG (with retry logic and CSPRNG fallback)
# ═══════════════════════════════════════════════════════════════════

class QuantumRNG:
    """
    Fetches unsigned 16-bit integers from the ANU QRNG API.
    Retries up to MAX_RETRIES times with linear back-off.
    Falls back to the OS CSPRNG (secrets module) if the API is
    unavailable or no key is configured.
    """

    def __init__(self, api_key: str, stats: SessionStats) -> None:
        self.api_key = api_key
        self.stats = stats

    def fetch(self, count: int) -> Tuple[List[int], str]:
        """
        Return (numbers, source) where source is "quantum" or "csprng".
        """
        if self.api_key:
            numbers = self._fetch_quantum(count)
            if numbers is not None:
                return numbers, "quantum"
        return self._fetch_csprng(count), "csprng"

    def _fetch_quantum(self, count: int) -> Optional[List[int]]:
        self.stats.api_calls += 1
        self.stats.numbers_requested += count

        for attempt in range(1, MAX_RETRIES + 1):
            conn: Optional[http.client.HTTPSConnection] = None
            t0 = time.monotonic()
            try:
                conn = http.client.HTTPSConnection(API_HOST, timeout=API_TIMEOUT)
                conn.request(
                    "GET",
                    f"/?length={count}&type=uint16&size=1",
                    headers={"x-api-key": self.api_key},
                )
                res = conn.getresponse()
                if res.status != 200:
                    raise OSError(f"HTTP {res.status}: {res.reason}")
                payload = json.loads(res.read().decode())
                numbers: List[int] = payload["data"]
                self.stats.numbers_received += len(numbers)
                self.stats.api_response_times.append(time.monotonic() - t0)
                return numbers

            except Exception as exc:
                msg = (
                    f"[{datetime.now():%H:%M:%S}] "
                    f"attempt {attempt}/{MAX_RETRIES} — {exc}"
                )
                self.stats.failed_requests.append(msg)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * attempt)
            finally:
                # BUG FIX: guard against conn being unset if the constructor throws
                if conn is not None:
                    conn.close()

        self.stats.api_errors += 1
        return None

    def _fetch_csprng(self, count: int) -> List[int]:
        """OS entropy fallback — cryptographically secure, not quantum."""
        self.stats.csprng_fallbacks += count
        return [secrets.randbelow(65536) for _ in range(count)]


# ═══════════════════════════════════════════════════════════════════
# Local RNG — debug only
# ═══════════════════════════════════════════════════════════════════

class LocalRNG:
    """
    Generates numbers locally using Python's random module.
    For debug/validation only — fast, no API calls, no entropy cost.
    Same interface as QuantumRNG so it drops straight into generate_hexagram().
    """

    def fetch(self, count: int) -> Tuple[List[int], str]:
        return [random.randrange(65536) for _ in range(count)], "local"


# ═══════════════════════════════════════════════════════════════════
# Line generation
# ═══════════════════════════════════════════════════════════════════

def make_line(num: int, stats: SessionStats) -> Line:
    """
    Map one uint16 to an I Ching line via classical yarrow-stalk probabilities.

    The yarrow-stalk procedure yields four possible values with these
    exact proportions (n = num % 16 gives a uniform draw from 0–15):

        Old Yang  (9)  — 3/16 = 18.75%  — yang, changing
        Young Yang (7) — 5/16 = 31.25%  — yang, stable
        Young Yin  (8) — 7/16 = 43.75%  — yin,  stable
        Old Yin    (6) — 1/16 =  6.25%  — yin,  changing

    BUG FIXED: the original code had Old Yin at 7/16 and Young Yin at 1/16
    — the yin probabilities were completely inverted.  In the traditional
    method yin-stable (8) is the most common outcome and yin-changing (6)
    is the rarest, giving the oracle its characteristic bias toward stability.
    """
    n = num % 16

    if n < 3:        # 0, 1, 2  → 3 values → Old Yang  (9)
        stats.line_type_counts["old_yang"] += 1
        return Line(yang=True,  changing=True,  line_type="old_yang")
    elif n < 8:      # 3–7     → 5 values → Young Yang (7)
        stats.line_type_counts["young_yang"] += 1
        return Line(yang=True,  changing=False, line_type="young_yang")
    elif n < 9:      # 8       → 1 value  → Old Yin    (6)
        stats.line_type_counts["old_yin"] += 1
        return Line(yang=False, changing=True,  line_type="old_yin")
    else:            # 9–15    → 7 values → Young Yin  (8)
        stats.line_type_counts["young_yin"] += 1
        return Line(yang=False, changing=False, line_type="young_yin")


def generate_hexagram(rng, stats: SessionStats) -> Tuple[Hexagram, str]:
    """Draw 6 lines and return (Hexagram, rng_source)."""
    numbers, source = rng.fetch(6)
    lines = [make_line(n, stats) for n in numbers]
    return Hexagram(lines), source


# ═══════════════════════════════════════════════════════════════════
# Formatting
# ═══════════════════════════════════════════════════════════════════

def format_reading(reading: Reading) -> str:
    """Render a complete reading as a printable string (no side-effects)."""
    hex_ = reading.hexagram
    ts = reading.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    src = "⚛  Quantum RNG" if reading.rng_source == "quantum" else "🔐 CSPRNG (local)"

    out: List[str] = [
        "",
        "┌─────────────────────────────────────────────────┐",
        "│              I CHING CONSULTATION               │",
        "└─────────────────────────────────────────────────┘",
        f"  {ts}  [{src}]",
    ]

    # ── Present hexagram ──────────────────────────────────────────
    out += ["", "  PRESENT HEXAGRAM"]
    out += hex_.display_lines(mark_changing=True)
    out += [f"", f"  ➤  {hex_.name()}"]

    # ── Trigrams ──────────────────────────────────────────────────
    t = hex_.trigrams()
    out += [
        "",
        "  TRIGRAM ANALYSIS",
        f"  Upper: {t['upper']['symbol']}  {t['upper']['name']:10s} — {t['upper']['meaning']}",
        f"  Lower: {t['lower']['symbol']}  {t['lower']['name']:10s} — {t['lower']['meaning']}",
    ]

    # ── Nuclear hexagram ──────────────────────────────────────────
    nuc = hex_.nuclear()
    nuc_t = nuc.trigrams()
    out += [
        "",
        "  NUCLEAR HEXAGRAM  (inner dynamic, lines 2–5)",
        *["  " + l.symbol for l in nuc.lines],
        f"",
        f"  ➤  {nuc.name()}",
        f"  Upper: {nuc_t['upper']['symbol']}  {nuc_t['upper']['name']}  ·  "
        f"Lower: {nuc_t['lower']['symbol']}  {nuc_t['lower']['name']}",
    ]

    # ── Line positions ────────────────────────────────────────────
    out += ["", "  LINE POSITIONS"]
    for i, line in enumerate(hex_.lines):
        pos = LINE_POSITIONS[i]
        polarity = "Yang" if line.yang else "Yin "
        note = "  ✦ changing" if line.changing else ""
        out.append(
            f"  {pos['position']}.  {polarity}  {pos['significance']:14s}"
            f"  ({pos['realm']}){note}"
        )

    # ── Changing lines & future hexagram ──────────────────────────
    changing = hex_.changing_line_numbers
    if changing:
        out += ["", f"  CHANGING LINES:  {', '.join(map(str, changing))}"]
        future = hex_.changed()
        fut_t = future.trigrams()
        out += [
            "",
            "  FUTURE HEXAGRAM  (after transformation)",
            *future.display_lines(mark_changing=False),
            f"",
            f"  ➤  {future.name()}",
            "",
            "  Future trigrams:",
            f"  Upper: {fut_t['upper']['symbol']}  {fut_t['upper']['name']:10s} — {fut_t['upper']['meaning']}",
            f"  Lower: {fut_t['lower']['symbol']}  {fut_t['lower']['name']:10s} — {fut_t['lower']['meaning']}",
        ]
    else:
        out += ["", "  No changing lines — the situation is stable."]

    out.append("")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════

def save_reading(text: str, directory: str = ".") -> Path:
    """Save a formatted reading to a timestamped text file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(directory) / f"iching_{ts}.txt"
    path.write_text(text, encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════
# Debug mode
# ═══════════════════════════════════════════════════════════════════

def run_debug(n: int) -> None:
    """Run N consultations silently using local random numbers and print stats."""
    print(f"\n  🐛 Debug mode — running {n:,} consultations locally…")
    stats = SessionStats(start_time=datetime.now())
    rng = LocalRNG()
    for _ in range(n):
        hex_, source = generate_hexagram(rng, stats)
        stats.record_reading(Reading(
            timestamp=datetime.now(), hexagram=hex_, rng_source=source
        ))
    stats.end_time = datetime.now()
    print(stats.summary())
    print()


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    # Debug mode: python iching_oracle.py --debug 1000
    if len(sys.argv) == 3 and sys.argv[1] == "--debug":
        run_debug(int(sys.argv[2]))
        return

    print("\n  ☰ Quantum I Ching Oracle — Yarrow-Stalk Method ☷")
    print("  " + "═" * 47)

    if not API_KEY:
        print(
            "\n  ⚠  ANU_QRNG_API_KEY not set.\n"
            "     Readings will use the OS CSPRNG (cryptographically secure,\n"
            "     but not quantum).  Get a free key at:\n"
            "     https://quantumnumbers.anu.edu.au\n"
        )

    stats = SessionStats(start_time=datetime.now())
    rng = QuantumRNG(API_KEY, stats)

    try:
        while True:
            input("  Press Enter to consult the oracle  (Ctrl+C to exit)…")
            print("  Drawing hexagram…")

            hex_, source = generate_hexagram(rng, stats)
            reading = Reading(
                timestamp=datetime.now(),
                hexagram=hex_,
                rng_source=source,
            )

            text = format_reading(reading)
            stats.record_reading(reading)
            print(text)

            action = input(
                "  [Enter] New reading   [s] Save   [q] Quit → "
            ).strip().lower()

            if action == "s":
                path = save_reading(text)
                print(f"  Saved → {path}\n")
                action = input("  [Enter] New reading   [q] Quit → ").strip().lower()

            if action == "q":
                break

    except KeyboardInterrupt:
        print("\n\n  Session ended by user.")
    finally:
        stats.end_time = datetime.now()
        print(stats.summary())
        print()


if __name__ == "__main__":
    main()