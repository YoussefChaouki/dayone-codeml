"""Patient linking: attach a new visit to an existing profile, never guess silently.

The key is the random code the midwife writes on the registry (``N° de la fiche``).
Codes are compared after OCR-confusion normalisation (O/0, I/1, S/5, B/8, Z/2) and with
an edit-distance tolerance, then the candidate is checked against non-identifying
attributes (age, last menstrual period, expected delivery date, gravidity / parity,
province). The midwife always decides: a plausible candidate is never bypassed by
creating a new profile automatically.

Internal patient ids are random (uuid4), never derived from personal information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz.distance import Levenshtein

PLAUSIBLE = 0.35  # at or above: the midwife must choose between candidates / new profile
STRONG = 0.85

_CONFUSIONS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8",
                             "Z": "2", "G": "6"})


def normalise_code(code: str | None) -> str:
    if not code:
        return ""
    return re.sub(r"[^A-Z0-9]", "", code.upper()).translate(_CONFUSIONS)


@dataclass
class Candidate:
    patient_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _date(v) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def score_candidate(query: dict, profile: dict) -> tuple[float, list[str], list[str]]:
    """``query`` / ``profile['quasi']``: code, age, lmp, edd, gravidity, parity, province (any may be missing)."""
    reasons, conflicts = [], []
    q_code = normalise_code(query.get("code"))
    codes = [normalise_code(c) for c in profile.get("codes", [])]
    score = 0.0
    if q_code and codes:
        d = min(Levenshtein.distance(q_code, c) for c in codes)
        if d == 0:
            score += 0.7
            reasons.append("même code")
        elif d == 1:
            score += 0.45
            reasons.append("code à 1 caractère près")
        elif d == 2 and len(q_code) >= 6:
            score += 0.25
            reasons.append("code à 2 caractères près")
    quasi = profile.get("quasi", {})
    qa, pa = query.get("age"), quasi.get("age")
    if isinstance(qa, int | float) and isinstance(pa, int | float):
        if abs(qa - pa) <= 1:
            score += 0.1
            reasons.append("même âge")
        elif abs(qa - pa) > 3:
            score -= 0.2
            conflicts.append(f"âge {pa} ≠ {qa}")
    for key, label, tol in (("edd", "DPA", 14), ("lmp", "DDR", 14)):
        qd, pd = _date(query.get(key)), _date(quasi.get(key))
        if qd and pd:
            if abs((qd - pd).days) <= tol:
                score += 0.12
                reasons.append(f"{label} cohérente")
            else:
                score -= 0.15  # may be the same woman in a new pregnancy: penalise, do not exclude
                conflicts.append(f"{label} différente")
    for key, label in (("gravidity", "gestité"), ("parity", "parité")):
        qv, pv = query.get(key), quasi.get(key)
        if isinstance(qv, int | float) and isinstance(pv, int | float):
            if qv == pv:
                score += 0.04
            elif qv < pv:
                score -= 0.1
                conflicts.append(f"{label} {pv} → {qv}")
    qp, pp = query.get("province"), quasi.get("province")
    if qp and pp:
        if str(qp).lower() == str(pp).lower():
            score += 0.04
        else:
            conflicts.append("province différente")
    return max(0.0, min(1.0, score)), reasons, conflicts


def find_candidates(query: dict, patients: dict[str, dict], limit: int = 2) -> list[Candidate]:
    cands = []
    for pid, profile in patients.items():
        s, reasons, conflicts = score_candidate(query, profile)
        if s >= PLAUSIBLE * 0.5:  # keep weak ones for display ranking, decision uses PLAUSIBLE
            quasi = profile.get("quasi", {})
            cands.append(Candidate(pid, round(s, 3), reasons, conflicts,
                                   {"code": (profile.get("codes") or [""])[-1], "age": quasi.get("age"),
                                    "edd": quasi.get("edd"), "gravidity": quasi.get("gravidity"),
                                    "parity": quasi.get("parity"), "visits": len(profile.get("records", []))}))
    cands.sort(key=lambda c: -c.score)
    return cands[:limit]


def plausible(cands: list[Candidate]) -> bool:
    return any(c.score >= PLAUSIBLE for c in cands)
