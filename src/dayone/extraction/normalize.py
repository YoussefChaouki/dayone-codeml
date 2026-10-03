"""Turn raw text (as read on paper) into canonical, typed values.

Handles French, English and Arabic surface forms (and Eastern Arabic digits) so that
"Neg", "Négatif", "Negative" and "سلبي" all become ``"negative"``. The same functions
normalise ground truth and predictions, so evaluation compares canonical values.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rapidfuzz import fuzz, process

from dayone.schema import FieldSpec, FieldStatus, ValueType

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

DASHES = {"-", "—", "–", "−", "_", "/", "--", "—-", "ـ", "x", "×"}
UNKNOWN_TOKENS = {"?", "??", "nsp", "inconnu", "inconnue", "unknown", "nc", "ne sait pas", "غير معروف",
                  "لا أعرف", "مجهول"}

# Closed vocabularies: canonical token -> surface forms (fr / en / ar). Matching is accent- and case-insensitive.
VOCABULARIES: dict[str, dict[str, list[str]]] = {
    "bool": {
        "yes": ["oui", "o", "yes", "y", "نعم", "اجل", "✓", "+", "fait"],
        "no": ["non", "n", "no", "لا", "ابدا", "0"],
    },
    "test_result": {
        "negative": ["neg", "nég", "négatif", "negatif", "négative", "negative", "-", "سلبي", "سلبية", "(-)"],
        "positive": ["pos", "pos +", "positif", "positive", "+", "(+)", "إيجابي", "ايجابي", "إيجابية"],
        "not_done": ["non fait", "nf", "not done", "لم يتم", "لم تجر"],
    },
    "immunity": {
        "immune": ["immune", "immunisée", "immunisee", "imm", "مناعة", "محصنة", "منيعة"],
        "non_immune": ["non immune", "non immunisée", "not immune", "nonimmune", "غير محصنة", "غير منيعة"],
    },
    "normal_finding": {
        "normal": ["ras", "normal", "normale", "normales", "normaux", "n", "aucun", "aucune", "néant", "neant",
                   "none", "nil", "nad", "عادي", "عادية", "طبيعي", "طبيعية", "لا شيء", "سليم"],
        "abnormal": ["anormal", "anormale", "abnormal", "pathologique", "غير طبيعي", "غير عادي"],
    },
    "conjunctiva": {
        "normal": ["normales", "normale", "normal", "colorées", "colorees", "ras", "عادية", "طبيعية"],
        "pale": ["pâles", "pales", "pâle", "pale", "décolorées", "decolorees", "شاحبة"],
    },
    "cervix": {
        "closed": ["fermé", "ferme", "closed", "مغلق"],
        "open": ["ouvert", "open", "مفتوح"],
        "modified": ["modifié", "modifie", "effacé", "efface", "ramolli", "changed", "متغير"],
    },
    "presentation": {
        "cephalic": ["céphalique", "cephalique", "cephalic", "sommet", "رأسي", "رأسية"],
        "breech": ["siège", "siege", "breech", "مقعدي", "مقعدية"],
        "transverse": ["transverse", "transversale", "épaule", "shoulder", "عرضي"],
    },
    "sex": {
        "F": ["f", "féminin", "feminin", "fille", "female", "girl", "أنثى", "انثى", "بنت"],
        "M": ["m", "masculin", "garçon", "garcon", "male", "boy", "ذكر", "ولد"],
    },
    "delivery_mode": {
        "vaginal": ["voie basse", "vb", "vaginal", "vaginale", "naturelle", "normal", "ولادة طبيعية", "طبيعية",
                    "طبيعي"],
        "instrumental": ["instrumentale", "voie basse instrumentale", "forceps", "ventouse", "instrumental"],
        "cesarean": ["césarienne", "cesarienne", "césar", "cs", "cesarean", "c-section", "caesarean", "قيصرية",
                     "عملية قيصرية"],
    },
    "education": {
        "none": ["aucun", "aucune", "analphabète", "analphabete", "néant", "none", "illiterate", "أمية", "لا شيء"],
        "primary": ["primaire", "primary", "ابتدائي"],
        "secondary": ["collège", "college", "lycée", "lycee", "secondaire", "secondary", "high school",
                      "middle school", "إعدادي", "اعدادي", "ثانوي"],
        "higher": ["supérieur", "superieur", "université", "universite", "universitaire", "higher", "university",
                   "جامعي", "عالي"],
    },
    "pap_smear": {
        "normal": ["normal", "normale", "ras", "négatif", "negatif", "negative", "عادي", "طبيعي"],
        "abnormal": ["anormal", "abnormal", "positif", "غير طبيعي"],
        "not_done": ["non fait", "not done", "nf", "jamais", "never", "لم يتم"],
    },
    # Free-text history fields: only the "nothing to report" forms are canonicalised.
    "history": {
        "none": ["ras", "aucun", "aucune", "néant", "neant", "rien", "none", "nil", "nothing", "no",
                 "لا شيء", "لاشيء", "لا يوجد", "عادي"],
    },
}

# CSV education code (0=none/primary, 1=secondary, 2=higher)
EDUCATION_CODE = {"none": 0, "primary": 0, "secondary": 1, "higher": 2}


def fold(text: str) -> str:
    """Case-, accent- and space-insensitive form used for all comparisons."""
    text = unicodedata.normalize("NFKD", text.translate(_ARABIC_DIGITS))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("œ", "oe").replace("’", "'")
    text = re.sub(r"[ـً-ٟ]", "", text)  # Arabic tatweel and diacritics
    text = re.sub(r"[^\w\s/+.,'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .,:;")


_VOCAB_INDEX: dict[str, dict[str, str]] = {
    name: {fold(form): canon for canon, forms in voc.items() for form in [canon, *forms]}
    for name, voc in VOCABULARIES.items()
}


@dataclass
class Parsed:
    status: FieldStatus
    value: Any = None
    ok: bool = True  # parsed cleanly into the expected type
    lexicon_score: float = 1.0  # similarity to the closest vocabulary entry (1 = exact)
    flags: list[str] = field(default_factory=list)


def clean_raw(text: str | None) -> str:
    """Remove OCR artefacts: markdown, dotted leaders, surrounding quotes."""
    if text is None:
        return ""
    text = text.translate(_ARABIC_DIGITS)
    text = re.sub(r"<[^>]+>", " ", text)  # html tags from table-mode OCR
    text = re.sub(r"[*`#|]", " ", text)
    text = re.sub(r"\.{3,}|…+", " ", text)  # dotted leaders printed on the form
    text = text.replace("\\", "/")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \"'“”:;")


def match_vocab(text: str, vocab: str, cutoff: float = 70.0) -> tuple[str | None, float]:
    """Closest canonical token of ``vocab`` for ``text`` and its similarity in [0, 1]."""
    key = fold(text)
    index = _VOCAB_INDEX[vocab]
    if key in index:
        return index[key], 1.0
    if not key:
        return None, 0.0
    best = process.extractOne(key, list(index), scorer=fuzz.ratio, score_cutoff=cutoff)
    if best is None:
        return None, 0.0
    return index[best[0]], best[1] / 100.0


_NUM = r"\d+(?:[.,]\d+)?"


def _parse_number(text: str) -> float | None:
    t = text.replace(" ", "") if re.fullmatch(r"[\d\s]+", text.strip()) else text
    m = re.search(_NUM, t)
    if not m:
        return None
    value = float(m.group(0).replace(",", "."))
    if re.search(r"\d\s*[kK]\b", t):  # "186k" platelets
        value *= 1000
    return value


def parse_date(text: str) -> date | None:
    t = re.sub(r"\s+", "", text.translate(_ARABIC_DIGITS))
    m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", t)
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def parse_bp(text: str) -> tuple[int, int] | None:
    t = re.sub(r"\s+", "", text.translate(_ARABIC_DIGITS))
    m = re.search(r"(\d{1,3})[/|\\\-](\d{1,3})", t)
    if not m:
        return None
    sys_, dia = int(m.group(1)), int(m.group(2))
    if sys_ < 30 and dia < 20:  # written in cmHg ("12/8")
        sys_, dia = sys_ * 10, dia * 10
    return sys_, dia


def repair_number(text: str, n: float, spec: FieldSpec) -> float | None:
    """Undo the systematic misreadings of handwritten numbers, if that lands in the plausible range.

    * a lost decimal separator ("791" for 79.1 kg, "372" for 37.2 °C, "093" for 0.93 g/L);
    * the unit "g" read as a trailing "9" ("36269" for 3626 g).
    Repaired values are flagged so they are never auto-accepted without a closer look.
    """
    lo, hi = spec.plausible  # caller checked it is set
    digits = re.sub(r"\D", "", text)
    candidates = []
    if spec.unit == "g" and digits.endswith("9") and len(digits) >= 4:
        candidates.append(float(digits[:-1]))
    if "." not in text and "," not in text and digits:
        candidates += [float(digits) / 10, float(digits) / 100]
    for c in candidates:
        if lo <= c <= hi:
            return c
    return None


def repair_bp(text: str) -> tuple[int, int] | None:
    """Blood pressure whose separator was lost or read as a digit ("137192", "13792")."""
    digits = re.sub(r"\D", "", text.translate(_ARABIC_DIGITS))
    if not 4 <= len(digits) <= 7:
        return None

    def ok(s: int, d: int) -> bool:
        return 70 <= s <= 220 and 40 <= d <= 140 and s > d

    cands = []
    for i in range(2, len(digits) - 1):
        if digits[i] in "17" and len(digits) >= 6:  # the slash itself was read as 1 or 7
            cands.append((int(digits[:i]), int(digits[i + 1:])))
        cands.append((int(digits[:i]), int(digits[i:])))
    good = [c for c in cands if ok(*c)]
    return good[0] if good else None


def special_status(text: str) -> FieldStatus | None:
    """Status implied by the text itself, independently of the field type."""
    key = fold(text)
    raw = text.strip()
    if raw in DASHES or key in DASHES or re.fullmatch(r"[-—–_−ـ]+", raw):
        return FieldStatus.NOT_APPLICABLE
    if key in UNKNOWN_TOKENS or raw in UNKNOWN_TOKENS:
        return FieldStatus.UNKNOWN
    return None


def parse_value(spec: FieldSpec, raw: str | None) -> Parsed:
    """Parse raw text read in ``spec``'s region. Empty text means the field is blank."""
    text = clean_raw(raw)
    if not text:
        return Parsed(FieldStatus.NOT_PROVIDED)
    special = special_status(text)
    if special is not None:
        return Parsed(special)

    vt = spec.value_type
    K = FieldStatus.KNOWN
    if vt == ValueType.DATE:
        d = parse_date(text)
        return Parsed(K, d.isoformat()) if d else Parsed(K, text, ok=False, flags=["date_unparsed"])
    if vt == ValueType.BP:
        bp = parse_bp(text)
        if bp is None:
            repaired = repair_bp(text)
            if repaired is not None:
                return Parsed(K, f"{repaired[0]}/{repaired[1]}", flags=["repaired"])
            return Parsed(K, text, ok=False, flags=["bp_unparsed"])
        flags = [] if (60 <= bp[0] <= 250 and 30 <= bp[1] <= 150 and bp[0] > bp[1]) else ["bp_implausible"]
        return Parsed(K, f"{bp[0]}/{bp[1]}", flags=flags)
    if vt in (ValueType.INT, ValueType.FLOAT, ValueType.GEST_AGE):
        n = _parse_number(text)
        if n is None:
            # e.g. "Aucun" in a count field
            canon, score = match_vocab(text, "history")
            if canon == "none" and vt == ValueType.INT:
                return Parsed(K, 0, lexicon_score=score)
            return Parsed(K, text, ok=False, flags=["number_unparsed"])
        flags = []
        if spec.plausible and not (spec.plausible[0] <= n <= spec.plausible[1]):
            repaired = repair_number(text, n, spec)
            if repaired is None:
                flags.append("out_of_range")
            else:
                n = repaired
                flags.append("repaired")
        value: Any = int(round(n)) if vt == ValueType.INT else round(n, 2)
        return Parsed(K, value, flags=flags)
    if vt == ValueType.BOOL:
        canon, score = match_vocab(text, "bool")
        if canon is None:
            return Parsed(K, text, ok=False, lexicon_score=score, flags=["bool_unparsed"])
        return Parsed(K, canon == "yes", lexicon_score=score)
    if vt == ValueType.CODE:
        code = re.sub(r"\s+", "", text).upper().strip("/")
        return Parsed(K, code, ok=bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-/]{2,}", code)))
    if vt == ValueType.ENUM:
        canon, score = match_vocab(text, spec.vocabulary or "")
        if canon is None:
            return Parsed(K, fold(text), ok=False, lexicon_score=score, flags=["not_in_vocabulary"])
        return Parsed(K, canon, lexicon_score=score)
    # free text
    if spec.vocabulary:
        # very short words ("RAS" read "eAs") are snapped more permissively; the lower lexicon
        # score lowers the confidence, so the midwife still sees them when in doubt
        canon, score = match_vocab(text, spec.vocabulary, cutoff=65.0 if len(fold(text)) <= 4 else 85.0)
        if canon is not None:
            return Parsed(K, canon, lexicon_score=score)
    return Parsed(K, text)


def canonical(spec: FieldSpec, value: Any) -> Any:
    """Comparable form of a parsed value (free text is folded)."""
    if value is None:
        return None
    if spec.value_type == ValueType.TEXT and isinstance(value, str):
        return fold(value)
    return value
