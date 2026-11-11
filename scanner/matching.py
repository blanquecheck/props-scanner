"""Strike matching: same game, same player, same stat, same line, same side meaning.

Rules (a wrong match is worse than a missed alert):
  * game_key, stat and line must be identical. These never fuzzy-match.
  * Player name must be an exact normalized match, a seeded alias, or a pair you
    approved in the UI. Those get confidence 1.0 and can alert.
  * Anything else that looks similar becomes a 'review' match with confidence < 1.
    Review matches never alert until approved.
  * If one name maps to two candidates in the same game/stat/line, nothing auto-matches.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .normalize import norm_name
from .venues import Contract

FUZZY_REVIEW_MIN = 0.80


@dataclass
class MatchResult:
    kalshi: Contract
    poly: Contract
    confidence: float
    method: str      # exact | alias | fuzzy
    status: str      # auto | review


def load_alias_csv(path: Path) -> set[tuple[str, str, str]]:
    """CSV columns: sport, kalshi_name, polymarket_name. Returns normalized triples."""
    out: set[tuple[str, str, str]] = set()
    if not path.exists():
        return out
    with path.open(newline="") as f:
        for row in csv.DictReader(r for r in f if not r.lstrip().startswith("#")):
            k, p = row.get("kalshi_name", "").strip(), row.get("polymarket_name", "").strip()
            if k and p:
                out.add((row.get("sport", "").strip().lower(), norm_name(k), norm_name(p)))
    return out


def name_similarity(a: str, b: str) -> float:
    """0-1 similarity. Same last name + one first name a prefix of the other
    ('alex sarr' / 'alexandre sarr', 'cam thomas' / 'cameron thomas') scores 0.9."""
    sim = SequenceMatcher(None, a, b).ratio()
    pa, pb = a.split(), b.split()
    if len(pa) >= 2 and len(pb) >= 2 and pa[1:] == pb[1:]:
        fa, fb = pa[0], pb[0]
        if fa != fb and (fa.startswith(fb) or fb.startswith(fa)) and min(len(fa), len(fb)) >= 2:
            sim = max(sim, 0.9)
    return sim


def match_contracts(kalshi: list[Contract], poly: list[Contract],
                    aliases: set[tuple[str, str, str]] | None = None,
                    rejected: set[tuple[str, str]] | None = None) -> list[MatchResult]:
    aliases = aliases or set()
    rejected = rejected or set()
    by_key: dict[tuple, list[Contract]] = defaultdict(list)
    for p in poly:
        by_key[(p.game_key, p.stat, round(p.line, 3))].append(p)

    results: list[MatchResult] = []
    for k in kalshi:
        cands = by_key.get((k.game_key, k.stat, round(k.line, 3)), [])
        if not cands:
            continue
        kn = k.player_norm
        strong: list[tuple[Contract, str]] = []
        for p in cands:
            if (k.market_id, p.market_id) in rejected:
                continue
            pn = p.player_norm
            if pn == kn:
                strong.append((p, "exact"))
            elif (k.sport, kn, pn) in aliases:
                strong.append((p, "alias"))
        if len(strong) == 1:
            p, method = strong[0]
            results.append(MatchResult(k, p, 1.0, method, "auto"))
            continue
        if len(strong) > 1:
            for p, method in strong:  # ambiguous: send all to review, never alert
                results.append(MatchResult(k, p, 0.5, method, "review"))
            continue
        for p in cands:
            if (k.market_id, p.market_id) in rejected:
                continue
            sim = name_similarity(kn, p.player_norm)
            if sim >= FUZZY_REVIEW_MIN:
                results.append(MatchResult(k, p, round(min(sim, 0.95), 3), "fuzzy", "review"))
    return _drop_poly_duplicates(results)


def _drop_poly_duplicates(results: list[MatchResult]) -> list[MatchResult]:
    """A Polymarket contract may auto-match at most one Kalshi contract."""
    count: dict[str, int] = defaultdict(int)
    for r in results:
        if r.status == "auto":
            count[r.poly.market_id] += 1
    for r in results:
        if r.status == "auto" and count[r.poly.market_id] > 1:
            r.status, r.confidence = "review", 0.5
    return results
