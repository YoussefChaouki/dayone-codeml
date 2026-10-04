"""Multilingual synthetic pages: the specimen pages re-written in Arabic, English or a mix.

The organisers' specimen is French only, while the grading mentions French, Arabic and
English handwriting. We re-render the specimen pages: the handwriting layer is removed
at the PDF level and every value is written again, in the target language, with an open
handwriting font (Arabic: Aref Ruqaa / Reem Kufi Ink / Marhey, Latin: Caveat / Gaegu /
Patrick Hand), at the same place. Tick marks are untouched. Ground truth follows exactly:
canonical values are unchanged for typed fields (dates, numbers, closed vocabularies),
free text becomes the written translation.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import numpy as np
import pymupdf

from dayone.evaluation.groundtruth import HAND_FONTS
from dayone.extraction.normalize import parse_value
from dayone.forms.layout import ALL_FIELDS
from dayone.schema import FieldKind, FieldStatus, ValueType

FONT_DIR = Path("artifacts/fonts")
ARABIC_FONTS = ["ArefRuqaa-Regular.ttf", "ReemKufiInk-Regular.ttf", "Marhey-VF.ttf"]
LATIN_FONTS = ["Caveat-VF.ttf", "Gaegu-Regular.ttf", "PatrickHand-Regular.ttf"]
EASTERN_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

# canonical value of a closed vocabulary -> surface form per language
SURFACE: dict[str, dict[str, dict[str, str]]] = {
    "bool": {"yes": {"fr": "Oui", "en": "Yes", "ar": "نعم"}, "no": {"fr": "Non", "en": "No", "ar": "لا"}},
    "test_result": {"negative": {"fr": "Neg", "en": "Negative", "ar": "سلبي"},
                    "positive": {"fr": "Pos +", "en": "Positive", "ar": "إيجابي"},
                    "not_done": {"fr": "Non fait", "en": "Not done", "ar": "لم يتم"}},
    "immunity": {"immune": {"fr": "Immune", "en": "Immune", "ar": "منيعة"},
                 "non_immune": {"fr": "Non immune", "en": "Not immune", "ar": "غير منيعة"}},
    "normal_finding": {"normal": {"fr": "RAS", "en": "Normal", "ar": "عادي"},
                       "abnormal": {"fr": "Anormal", "en": "Abnormal", "ar": "غير طبيعي"}},
    "conjunctiva": {"normal": {"fr": "Normales", "en": "Normal", "ar": "عادية"},
                    "pale": {"fr": "Pâles", "en": "Pale", "ar": "شاحبة"}},
    "cervix": {"closed": {"fr": "Fermé", "en": "Closed", "ar": "مغلق"},
               "open": {"fr": "Ouvert", "en": "Open", "ar": "مفتوح"}},
    "presentation": {"cephalic": {"fr": "Céphalique", "en": "Cephalic", "ar": "رأسي"},
                     "breech": {"fr": "Siège", "en": "Breech", "ar": "مقعدي"}},
    "sex": {"F": {"fr": "F", "en": "Female", "ar": "أنثى"}, "M": {"fr": "M", "en": "Male", "ar": "ذكر"}},
    "delivery_mode": {"vaginal": {"fr": "Voie basse", "en": "Vaginal", "ar": "طبيعية"},
                      "cesarean": {"fr": "Césarienne", "en": "Cesarean", "ar": "قيصرية"},
                      "instrumental": {"fr": "Instrumentale", "en": "Instrumental", "ar": "instrumentale"}},
    "education": {"none": {"fr": "Aucun", "en": "None", "ar": "أمية"},
                  "primary": {"fr": "Primaire", "en": "Primary", "ar": "ابتدائي"},
                  "secondary": {"fr": "Lycée", "en": "High school", "ar": "ثانوي"},
                  "higher": {"fr": "Supérieur", "en": "University", "ar": "جامعي"}},
    "pap_smear": {"normal": {"fr": "Normal", "en": "Normal", "ar": "عادي"},
                  "abnormal": {"fr": "Anormal", "en": "Abnormal", "ar": "غير طبيعي"},
                  "not_done": {"fr": "Non fait", "en": "Not done", "ar": "لم يتم"}},
    "history": {"none": {"fr": "RAS", "en": "None", "ar": "لا شيء"}},
}

# free-text values found in the specimen -> (English, Arabic)
FREE_TEXT: dict[str, tuple[str, str]] = {
    "rabat-sale-kenitra": ("Rabat-Sale-Kenitra", "الرباط-سلا-القنيطرة"),
    "fes-meknes": ("Fes-Meknes", "فاس-مكناس"),
    "marrakech-safi": ("Marrakech-Safi", "مراكش-آسفي"),
    "beni mellal-khenifra": ("Beni Mellal-Khenifra", "بني ملال-خنيفرة"),
    "souss-massa": ("Souss-Massa", "سوس-ماسة"),
    "tanger-tetouan-al hoceima": ("Tangier-Tetouan-Al Hoceima", "طنجة-تطوان-الحسيمة"),
    "oriental": ("Oriental", "الشرق"),
    "casablanca-settat": ("Casablanca-Settat", "الدار البيضاء-سطات"),
    "draa-tafilalet": ("Draa-Tafilalet", "درعة-تافيلالت"),
    "kenitra": ("Kenitra", "القنيطرة"), "meknes": ("Meknes", "مكناس"), "al haouz": ("Al Haouz", "الحوز"),
    "azilal": ("Azilal", "أزيلال"), "taroudant": ("Taroudant", "تارودانت"), "chefchaouen": ("Chefchaouen", "شفشاون"),
    "berkane": ("Berkane", "بركان"), "settat": ("Settat", "سطات"), "errachidia": ("Errachidia", "الرشيدية"),
    "khemisset": ("Khemisset", "الخميسات"),
    "cycles reguliers": ("Regular cycles", "دورة منتظمة"),
    "asthme leger": ("Mild asthma", "ربو خفيف"),
    "appendicectomie": ("Appendectomy", "استئصال الزائدة"),
    "pere": ("Father", "الأب"), "mere": ("Mother", "الأم"), "oncle": ("Uncle", "العم"),
    "maternite": ("Maternity", "دار الولادة"),
    "souffrance foetale": ("Fetal distress", "ضائقة جنينية"),
    "uterus cicatriciel": ("Scarred uterus", "رحم مندوب"),
    "pre-eclampsie severe": ("Severe pre-eclampsia", "تسمم الحمل الشديد"),
    "propre": ("Clean", "نظيفة"), "propre, seche": ("Clean, dry", "نظيفة وجافة"),
    "souhaite en discuter avec son mari": ("Wants to discuss with her husband", "تريد مناقشة الأمر مع زوجها"),
    "poursuivre l'allaitement exclusif": ("Continue exclusive breastfeeding", "مواصلة الرضاعة الطبيعية الحصرية"),
    "fer 2 cp/j": ("Iron 2 tab/day", "حديد قرصان في اليوم"),
    "dr benjelloun": ("Dr Benjelloun", "د. بنجلون"), "dr chakir": ("Dr Chakir", "د. شاكر"),
    "sage-femme": ("Midwife", "قابلة"), "inf. zahra": ("Nurse Zahra", "الممرضة زهرة"),
    "sage-femme salima": ("Midwife Salima", "القابلة سليمة"), "sage-femme hajar": ("Midwife Hajar", "القابلة هاجر"),
    "neant": ("None", "لا شيء"), "ras": ("None", "لا شيء"), "aucune": ("None", "لا شيء"), "aucun": ("None", "لا شيء"),
}
UNIT_WORDS = {"SA": {"en": "wks", "ar": "أسبوع"}, "jours": {"en": "days", "ar": "أيام"}}


def _fold_key(text: str) -> str:
    from dayone.extraction.normalize import fold

    return fold(text.replace("�", "e"))


def surface(spec_id: str, gt: dict, lang: str, rng: random.Random) -> str | None:
    """Text to write for a ground-truth field in ``lang`` (None: keep the field blank)."""
    spec = ALL_FIELDS[spec_id]
    raw = gt["raw"].replace("�", "é")
    if gt["status"] == FieldStatus.NOT_APPLICABLE.value:
        return "—"
    if gt["status"] != FieldStatus.KNOWN.value:
        return None
    if lang == "fr":
        return raw
    vt = spec.value_type
    if vt in (ValueType.DATE, ValueType.BP, ValueType.INT, ValueType.FLOAT, ValueType.GEST_AGE, ValueType.CODE):
        text = raw
        for unit, tr in UNIT_WORDS.items():
            text = re.sub(rf"\b{unit}\b", tr[lang], text)
        if lang == "ar" and vt != ValueType.CODE and rng.random() < 0.25:
            text = text.translate(EASTERN_DIGITS)
        return text
    vocab = "bool" if vt == ValueType.BOOL else spec.vocabulary
    if vocab in SURFACE and gt["value"] in SURFACE[vocab]:
        return SURFACE[vocab][str(gt["value"])][lang] if not isinstance(gt["value"], bool) else \
            SURFACE["bool"]["yes" if gt["value"] else "no"][lang]
    if isinstance(gt["value"], bool):
        return SURFACE["bool"]["yes" if gt["value"] else "no"][lang]
    tr = FREE_TEXT.get(_fold_key(raw))
    if tr is None:
        return raw  # proper nouns (facility names...) are written the same way
    return tr[0] if lang == "en" else tr[1]


_OFFSETS: dict[tuple[str, float, bool], float] = {}


def _top_offset(font: str, size: float, css: str, archive: pymupdf.Archive, rtl: bool) -> float:
    """Distance (pt) between the top of an html box and the top of the ink it draws.

    Measured on rendered pixels, once per (font, size), on a fixed reference string: some
    Arabic fonts report unusable metrics, pixels do not lie.
    """
    key = (font, round(size, 1), rtl)
    if key not in _OFFSETS:
        html = '<p dir="rtl">عادية سلبي</p>' if rtl else "<p>Normal 12/04</p>"
        tmp = pymupdf.open()
        pg = tmp.new_page(width=400, height=100)
        pg.insert_htmlbox(pymupdf.Rect(10, 10, 390, 90), html, css=css, archive=archive)
        pix = pg.get_pixmap(dpi=144, colorspace=pymupdf.csGRAY)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        rows = np.where((a < 160).any(axis=1))[0]
        _OFFSETS[key] = (rows[0] / 2.0 - 10) if len(rows) else 0.0
    return _OFFSETS[key]


def render_page(src_pdf: pymupdf.Document, page_index: int, gt_page: dict, mode: str, seed: int,
                dpi: int = 200, latin_fonts: list[str] | None = None) -> tuple[np.ndarray, dict, dict]:
    """Rewrite one specimen page in ``mode`` (fr | en | ar | mixed); return BGR image, gt fields, languages."""
    import cv2

    rng = random.Random(seed)
    doc = pymupdf.open()
    doc.insert_pdf(src_pdf, from_page=page_index, to_page=page_index)
    page = doc[0]
    ink = None
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["font"].split("+")[-1].startswith(HAND_FONTS):
                    ink = ink or span["color"]
                    page.add_redact_annot(pymupdf.Rect(span["bbox"]), fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    color = f"#{ink if ink is not None else 0x1F2A8C:06x}"
    archive = pymupdf.Archive(str(FONT_DIR))
    gt_fields, langs, dropped = {}, {}, []
    for fid, gt in gt_page["fields"].items():
        spec = ALL_FIELDS[fid]
        if spec.kind == FieldKind.CHECKBOX:
            gt_fields[fid] = gt
            continue
        lang = rng.choice(["fr", "en", "ar"]) if mode == "mixed" else mode
        text = surface(fid, gt, lang, rng)
        if text is None:
            gt_fields[fid] = gt
            continue
        is_ar = bool(re.search(r"[؀-ۿ]", text))
        font = rng.choice(ARABIC_FONTS if is_ar else (latin_fonts or LATIN_FONTS))
        size = rng.uniform(10.5, 13.0) if not is_ar else rng.uniform(11.0, 13.5)
        css = (f"@font-face {{font-family: hw; src: url({font});}} "
               f"* {{font-family: hw; font-size: {size:.1f}px; color: {color}; margin: 0; line-height: 1.1;}}")
        html = f'<p dir="{"rtl" if is_ar else "ltr"}" style="text-align: left;">{text}</p>'
        dy = _top_offset(font, size, css, archive, is_ar)
        if "bbox_page" in gt:  # write where the original value was written
            bx0, by0, bx1, by1 = gt["bbox_page"]
            rect = pymupdf.Rect(bx0, by0 + 2 - dy, max(spec.region[2] - 1, bx0 + 20), by1 - dy + 14)
        else:
            x0, y0, x1, y1 = spec.region
            rect = pymupdf.Rect(x0 + 2, y0 - dy, x1 - 1, y1 + 2)
        spare = page.insert_htmlbox(rect, html, css=css, archive=archive, scale_low=0.6)
        if spare[0] < 0:  # did not fit: the original ink is already erased, so the field leaves the ground truth
            dropped.append(fid)
            continue
        parsed = parse_value(spec, text)
        gt_fields[fid] = {"status": parsed.status.value, "value": parsed.value, "raw": text}
        langs[fid] = "ar" if is_ar else lang if lang != "ar" else "ar"
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    if dropped:
        langs["_dropped"] = dropped
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR), gt_fields, langs


def main() -> None:
    import argparse

    import cv2

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path, default=Path("data/Paper Registry/dossiers_specimen_10_patientes.pdf"))
    ap.add_argument("--gt", type=Path, default=Path("artifacts/groundtruth/specimen.json"))
    ap.add_argument("--out", type=Path, default=Path("artifacts/synth"))
    args = ap.parse_args()
    gt_pages = json.loads(args.gt.read_text())
    src = pymupdf.open(args.pdf)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    # every page of every patient, once per mode; split stays by patient (see dataset.py)
    for gt_page in gt_pages:
        for mode in ("ar", "en", "mixed"):
            seed = gt_page["page_number"] * 101 + ["ar", "en", "mixed"].index(mode)
            img, fields, langs = render_page(src, gt_page["page_number"] - 1, gt_page, mode, seed)
            name = f"synth_p{gt_page['page_number']:02d}_{mode}.png"
            cv2.imwrite(str(args.out / name), img)
            manifest.append({**{k: gt_page[k] for k in ("page_number", "patient", "page_type")},
                             "image": name, "mode": mode, "fields": fields, "languages": langs})
    (args.out / "synth.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"{len(manifest)} synthetic pages -> {args.out}")


if __name__ == "__main__":
    main()
