"""Field layout of the paper registry ("Fiche de surveillance de la grossesse et du post-partum").

Coordinates are PDF points on the reference page (A4, 595 x 842 pt), measured on the
organisers' specimen (patient 1). Photos are registered onto this reference before
regions are cropped, so the same coordinates serve every capture.

Direct identifiers (name, husband's name, national ID, phone, address) are NOT fields:
they are listed in ``PII_ZONES`` and redacted from images before anything is stored.
"""

from __future__ import annotations

from dayone.schema import FieldKind, FieldSpec, PageType, ValueType

BOX = 8.0  # tick box side in points
Rect = tuple[float, float, float, float]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(page: PageType, section: str, id_: str, fr: str, en: str, region: Rect,
          vt: ValueType = ValueType.TEXT, **kw) -> FieldSpec:
    return FieldSpec(id=f"{page.value}.{id_}", page_type=page, section=section, label_fr=fr,
                     label_en=en, kind=FieldKind.TEXT, value_type=vt, region=region, **kw)


def _box(page: PageType, section: str, id_: str, fr: str, en: str, x: float, y: float,
         group: str | None = None, option: str | None = None, exclusive: bool = False) -> FieldSpec:
    return FieldSpec(id=f"{page.value}.{id_}", page_type=page, section=section, label_fr=fr,
                     label_en=en, kind=FieldKind.CHECKBOX, value_type=ValueType.BOOL,
                     region=(x, y, x + BOX, y + BOX), group=group, option=option, exclusive=exclusive)


def _group(page: PageType, section: str, group: str, exclusive: bool,
           options: list[tuple[str, str, str, float, float]]) -> list[FieldSpec]:
    """options: (option, label_fr, label_en, x, y)."""
    return [_box(page, section, f"{group}.{opt}", fr, en, x, y, group=f"{page.value}.{group}",
                 option=opt, exclusive=exclusive) for opt, fr, en, x, y in options]


# ---------------------------------------------------------------------------
# Page 1 — cover
# ---------------------------------------------------------------------------
P = PageType.COVER
COVER = [
    _text(P, "facility", "registry_code", "N° de la fiche (code)", "Registry code",
          (106, 93, 300, 118), ValueType.CODE),
    _text(P, "facility", "region", "Région", "Region", (84, 128, 318, 152)),
    _text(P, "facility", "province", "Province", "Province", (360, 128, 560, 152)),
    _text(P, "facility", "facility_name", "Établissement sanitaire", "Health facility",
          (174, 163, 560, 187)),
    *_group(P, "facility", "facility_type", True, [
        ("DR", "DR", "DR", 91, 237), ("CSC", "CSC", "CSC", 231, 238), ("CSU", "CSU", "CSU", 351, 238),
        ("CSCA", "CSCA", "CSCA", 90, 267), ("CSUA", "CSUA", "CSUA", 230, 268)]),
    *_group(P, "facility", "coverage", True, [
        ("fixed", "Fixe", "Fixed", 90, 322), ("mobile", "Mobile", "Mobile", 230, 323)]),
    _box(P, "risk", "high_risk", "Grossesse classée à risque", "High-risk pregnancy", 60, 440),
    *_group(P, "risk", "risk_type", False, [
        ("anemia", "Anémie", "Anaemia", 65, 487), ("hypertension", "HTA", "Hypertension", 65, 505),
        ("diabetes", "Diabète", "Diabetes", 65, 523), ("cardiopathy", "Cardiopathie", "Heart disease", 65, 541),
        ("metrorrhagia", "Métrorragie", "Metrorrhagia", 290, 488),
        ("infection", "Infection", "Infection", 290, 506),
        ("preeclampsia", "Pré-éclampsie", "Pre-eclampsia", 290, 524),
        ("eclampsia", "Éclampsie", "Eclampsia", 290, 542)]),
    _text(P, "risk", "risk_other", "Autre risque", "Other risk", (362, 552, 560, 577)),
]

# ---------------------------------------------------------------------------
# Page 2 — identification and history
# ---------------------------------------------------------------------------
P = PageType.HISTORY
_HER_ROWS = [("hypertension", "HTA", "Hypertension", 237), ("diabetes", "Diabète", "Diabetes", 257),
             ("hereditary", "Maladies héréditaires", "Hereditary diseases", 277),
             ("malformations", "Malformations", "Malformations", 297),
             ("allergies", "Allergie(s)", "Allergies", 317)]
_HER_COLS = [("woman_family", "famille de la femme", "woman's family", 131, 211),
             ("husband_family", "mari/famille", "husband/family", 211, 291)]
_OBS_ROWS = [("abortion", "Avortement", "Abortion", 382), ("preterm", "Accouchement prématuré", "Preterm delivery", 402),
             ("stillbirth", "Mort fœtale in utéro", "Intrauterine fetal death", 422),
             ("other", "Autre anomalie", "Other anomaly", 442)]
_OBS_COLS = [("count", "nombre", "count", 150, 220, ValueType.INT, (0, 20)),
             ("date", "date", "date", 220, 330, ValueType.TEXT, None),
             ("place", "lieu", "place", 330, 420, ValueType.TEXT, None),
             ("ga", "âge gestationnel", "gestational age", 420, 555, ValueType.GEST_AGE, (4, 44))]
_PREV_ROWS = [("date", "Date", "Date", 531, ValueType.DATE, None, None),
              ("mode", "Modalité d'extraction", "Delivery mode", 559, ValueType.ENUM, None, "delivery_mode"),
              ("cs_indication", "Indication de césarienne", "Cesarean indication", 587, ValueType.TEXT, None, None),
              ("complication", "Complication", "Complication", 615, ValueType.TEXT, None, None),
              ("weight", "Poids du nouveau-né", "Newborn weight", 643, ValueType.INT, (300, 6500), None),
              ("nb_complication", "Complication du nouveau-né", "Newborn complication", 671, ValueType.TEXT, None, None)]
_PREV_COLS = [150, 231, 312, 393, 474, 555]

HISTORY = [
    _text(P, "profile", "age", "Âge", "Age", (74, 76, 296, 96), ValueType.INT, unit="ans", plausible=(12, 55)),
    _text(P, "profile", "education", "Niveau d'instruction", "Education level", (128, 97, 296, 118),
          ValueType.ENUM, vocabulary="education"),
    _box(P, "profile", "consanguinity", "Consanguinité", "Consanguinity", 50, 173),
    _box(P, "profile", "desired_pregnancy", "Grossesse désirée", "Desired pregnancy", 302, 175),
    *[_text(P, "family_history", f"family.{r}.{c}", f"{rf} — {cf}", f"{re_} — {ce}",
            (x0, y - 6, x1, y + 15), vocabulary="history")
      for r, rf, re_, y in _HER_ROWS for c, cf, ce, x0, x1 in _HER_COLS],
    _text(P, "woman_history", "woman.medical", "Antécédents médicaux", "Medical history",
          (311, 230, 376, 336), vocabulary="history"),
    _text(P, "woman_history", "woman.surgical", "Antécédents chirurgicaux", "Surgical history",
          (376, 230, 456, 336), vocabulary="history"),
    _text(P, "woman_history", "woman.gynecological", "Antécédents gynécologiques", "Gynaecological history",
          (456, 230, 556, 336), vocabulary="history"),
    *[_text(P, "obstetric_history", f"anomaly.{r}.{c}", f"{rf} — {cf}", f"{re_} — {ce}",
            (x0, y - 6, x1, y + 14), vt, plausible=pl, row=r, col=c)
      for r, rf, re_, y in _OBS_ROWS for c, cf, ce, x0, x1, vt, pl in _OBS_COLS],
    *[_text(P, "previous_deliveries", f"prev{k + 1}.{r}", f"Accouchement {k + 1} — {rf}",
            f"Delivery {k + 1} — {re_}", (_PREV_COLS[k], y - 11, _PREV_COLS[k + 1], y + 16), vt,
            plausible=pl, vocabulary=voc, row=r, col=f"prev{k + 1}", unit="g" if r == "weight" else None)
      for k in range(5) for r, rf, re_, y, vt, pl, voc in _PREV_ROWS],
    _text(P, "obstetric_summary", "gravidity", "Gestité", "Gravidity", (80, 707, 155, 732), ValueType.INT,
          plausible=(1, 20)),
    _text(P, "obstetric_summary", "parity", "Parité", "Parity", (186, 708, 276, 733), ValueType.INT,
          plausible=(0, 20)),
    _text(P, "obstetric_summary", "living_children", "Enfants vivants", "Living children",
          (376, 708, 470, 734), ValueType.INT, plausible=(0, 20)),
    *_group(P, "vaccination", "tetanus_doses", False, [
        (str(k), f"VAT {k}", f"Tetanus dose {k}", x, 740) for k, x in [(1, 73), (2, 113), (3, 153), (4, 193), (5, 233)]]),
    _box(P, "vaccination", "rubella_vaccinated", "Vaccinée contre la rubéole", "Rubella vaccinated", 38, 759),
    _text(P, "vaccination", "rubella_date", "Date vaccin rubéole", "Rubella vaccine date", (311, 755, 430, 774),
          ValueType.DATE),
    _box(P, "vaccination", "hepb_vaccinated", "Vaccinée contre l'hépatite B", "Hepatitis B vaccinated", 38, 775),
    _text(P, "vaccination", "hepb_date", "Date vaccin hépatite B", "Hepatitis B vaccine date",
          (311, 771, 430, 790), ValueType.DATE),
    _text(P, "screening", "pap_smear", "Frottis cervical / IVA", "Pap smear / VIA", (178, 786, 420, 809),
          ValueType.ENUM, vocabulary="pap_smear"),
]

# ---------------------------------------------------------------------------
# Page 3 — current pregnancy (longitudinal visits table)
# ---------------------------------------------------------------------------
P = PageType.PREGNANCY
VISIT_COLS = [("t1v1", "T1 visite 1", "T1 visit 1"), ("t1v2", "T1 visite 2", "T1 visit 2"),
              ("t1v3", "T1 visite 3", "T1 visit 3"), ("t2v1", "T2 visite 1", "T2 visit 1"),
              ("t2v2", "T2 visite 2", "T2 visit 2"), ("t2v3", "T2 visite 3", "T2 visit 3"),
              ("m7", "7e mois", "Month 7"), ("m8", "8e mois", "Month 8"), ("m9", "9e mois", "Month 9")]
VISIT_X = [152, 197, 242, 286, 331, 376, 421, 466, 510, 555]
# (row key, label fr, label en, y0, y1, value type, plausible, vocabulary)
VISIT_ROWS = [
    ("appointment", "Rendez-vous", "Appointment", 138, 156, ValueType.DATE, None, None),
    ("visit_date", "Venue le", "Visit date", 156, 174, ValueType.DATE, None, None),
    ("reminder", "Visite de relance", "Reminder visit", 174, 191, ValueType.BOOL, None, None),
    ("gest_age", "Âge gestationnel", "Gestational age", 191, 209, ValueType.GEST_AGE, (4, 44), None),
    ("weight", "Poids (kg)", "Weight (kg)", 227, 245, ValueType.FLOAT, (30, 150), None),
    ("bp", "TA", "Blood pressure", 245, 263, ValueType.BP, None, None),
    ("skeleton", "Anomalies squelette", "Skeletal anomalies", 263, 280, ValueType.ENUM, None, "normal_finding"),
    ("conjunctiva", "État des conjonctives", "Conjunctiva", 280, 298, ValueType.ENUM, None, "conjunctiva"),
    ("breasts", "Examen des seins", "Breast exam", 298, 316, ValueType.ENUM, None, "normal_finding"),
    ("edema", "Œdèmes", "Oedema", 316, 334, ValueType.BOOL, None, None),
    ("fetal_movements", "Mouvements actifs", "Fetal movements", 334, 352, ValueType.BOOL, None, None),
    ("fundal_height", "Hauteur utérine (cm)", "Fundal height (cm)", 352, 369, ValueType.FLOAT, (5, 45), None),
    ("fetal_heart_rate", "BCF", "Fetal heart rate", 369, 387, ValueType.INT, (90, 200), None),
    ("speculum", "Examen au spéculum", "Speculum exam", 387, 405, ValueType.ENUM, None, "normal_finding"),
    ("cervix", "TV : état du col", "Cervix", 405, 423, ValueType.ENUM, None, "cervix"),
    ("presentation", "TV : présentation", "Presentation", 423, 441, ValueType.ENUM, None, "presentation"),
    ("pelvis", "TV : bassin", "Pelvis", 441, 458, ValueType.ENUM, None, "normal_finding"),
    ("glycosuria", "Glucosurie", "Glycosuria", 476, 494, ValueType.ENUM, None, "test_result"),
    ("albuminuria", "Albuminurie", "Albuminuria", 494, 512, ValueType.ENUM, None, "test_result"),
    ("rubella", "Rubéole", "Rubella", 512, 530, ValueType.ENUM, None, "immunity"),
    ("toxoplasmosis", "Toxoplasmose", "Toxoplasmosis", 530, 547, ValueType.ENUM, None, "immunity"),
    ("syphilis", "Syphilis (TPHA/VDRL)", "Syphilis (TPHA/VDRL)", 547, 565, ValueType.ENUM, None, "test_result"),
    ("hbsag", "Ag HBs", "HBs antigen", 565, 583, ValueType.ENUM, None, "test_result"),
    ("hiv", "Sérologie VIH", "HIV serology", 583, 601, ValueType.ENUM, None, "test_result"),
    ("hemoglobin", "Hémoglobine (g/dL)", "Haemoglobin (g/dL)", 601, 619, ValueType.FLOAT, (4, 20), None),
    ("platelets", "Plaquettes (/mm³)", "Platelets (/mm³)", 619, 636, ValueType.INT, (20_000, 800_000), None),
    ("glycemia", "Glycémie (g/L)", "Glycaemia (g/L)", 636, 654, ValueType.FLOAT, (0.3, 4.0), None),
    ("rai", "RAI (si Rh négatif)", "Irregular antibodies (Rh-)", 654, 672, ValueType.ENUM, None, "test_result"),
    ("iron", "Fer", "Iron", 690, 708, ValueType.BOOL, None, None),
    ("examiner", "Examen fait par", "Examined by", 725, 743, ValueType.TEXT, None, None),
]
VISIT_ROW_KEYS = [r[0] for r in VISIT_ROWS]
_UNITS = {"weight": "kg", "fundal_height": "cm", "fetal_heart_rate": "bpm", "hemoglobin": "g/dL",
          "glycemia": "g/L", "platelets": "/mm³", "bp": "mmHg"}

PREGNANCY = [
    _text(P, "dating", "lmp_date", "Date des dernières règles (DDR)", "Last menstrual period", (65, 62, 186, 85),
          ValueType.DATE),
    _text(P, "dating", "height", "Taille (cm)", "Height (cm)", (217, 62, 318, 85), ValueType.INT, unit="cm",
          plausible=(120, 200)),
    *_group(P, "dating", "blood_group", True, [
        ("A", "A", "A", 375, 71), ("B", "B", "B", 407, 71), ("O", "O", "O", 439, 71), ("AB", "AB", "AB", 471, 71)]),
    *_group(P, "dating", "rhesus", True, [("neg", "Rh-", "Rh-", 505, 71), ("pos", "Rh+", "Rh+", 505, 85)]),
    _text(P, "dating", "edd", "Date prévue d'accouchement", "Expected delivery date", (182, 81, 268, 102),
          ValueType.DATE),
    _text(P, "dating", "term_exceeded_date", "Date de dépassement de terme", "Post-term date",
          (418, 81, 500, 102), ValueType.DATE),
    *[_text(P, "visits", f"visit.{c}.{r}", f"{rf} — {cf}", f"{re_} — {ce}",
            (VISIT_X[k] + 1, y0 + 1, VISIT_X[k + 1] - 1, y1 - 1), vt, plausible=pl, vocabulary=voc,
            unit=_UNITS.get(r), row=r, col=c)
      for k, (c, cf, ce) in enumerate(VISIT_COLS) for r, rf, re_, y0, y1, vt, pl, voc in VISIT_ROWS],
]

# ---------------------------------------------------------------------------
# Page 4 — delivery
# ---------------------------------------------------------------------------
P = PageType.DELIVERY
DELIVERY = [
    _box(P, "place", "place.supervised", "En milieu surveillé", "In a supervised facility", 38, 140),
    *_group(P, "place", "facility", True, [
        ("birth_house", "Maison d'accouchement", "Birth house", 213, 134),
        ("maternity", "Maternité", "Maternity ward", 213, 150),
        ("private_clinic", "Clinique privée", "Private clinic", 213, 166)]),
    _text(P, "place", "place.facility_other", "Autre lieu surveillé", "Other supervised place", (250, 177, 560, 200)),
    _box(P, "place", "place.home", "À domicile", "At home", 39, 230),
    _box(P, "place", "place.home_assisted", "Assisté par un personnel qualifié", "Assisted by skilled staff", 214, 229),
    _text(P, "place", "place.home_other", "Autre (domicile)", "Other (home)", (250, 243, 560, 266)),
    _text(P, "delivery", "date", "Date de l'accouchement", "Delivery date", (132, 289, 330, 314), ValueType.DATE),
    *_group(P, "delivery", "mode", True, [
        ("vaginal", "Voie basse non instrumentale", "Spontaneous vaginal", 214, 344),
        ("instrumental", "Voie basse instrumentale", "Instrumental vaginal", 215, 362),
        ("cesarean_planned", "Césarienne programmée", "Planned cesarean", 215, 430),
        ("cesarean_emergency", "Césarienne en urgence", "Emergency cesarean", 390, 428)]),
    *_group(P, "delivery", "instrument", False, [
        ("forceps", "Forceps", "Forceps", 250, 377), ("vacuum", "Ventouse", "Vacuum", 250, 393),
        ("episiotomy", "Épisiotomie", "Episiotomy", 250, 409)]),
    _text(P, "delivery", "cesarean_indication", "Indication de la césarienne", "Cesarean indication",
          (292, 440, 560, 464)),
    _box(P, "complications", "complications.present", "Présence de complications", "Complications present", 191, 499),
    *_group(P, "complications", "complication_time", False, [
        ("delivery", "Au moment de l'accouchement", "At delivery", 216, 502),
        ("postpartum", "Suites de couches", "Postpartum", 216, 518)]),
    *_group(P, "complications", "complication_type", False, [
        ("preeclampsia", "Pré-éclampsie", "Pre-eclampsia", 251, 553),
        ("eclampsia", "Éclampsie", "Eclampsia", 251, 568),
        ("hemorrhage", "Hémorragie", "Haemorrhage", 251, 583),
        ("infection", "Infection", "Infection", 251, 598),
        ("other", "Autres", "Other", 251, 613)]),
    _text(P, "complications", "complications.other_text", "Autre complication", "Other complication",
          (338, 624, 560, 648)),
    *_group(P, "newborn", "newborn_status", True, [
        ("alive", "Vivant", "Alive", 217, 679), ("stillborn", "Mort-né", "Stillborn", 287, 678),
        ("death_24h", "Décès < 24 h", "Death < 24 h", 367, 677)]),
    _text(P, "newborn", "newborn.sex", "Sexe", "Sex", (243, 695, 330, 717), ValueType.ENUM, vocabulary="sex"),
    _text(P, "newborn", "newborn.weight", "Poids de naissance (g)", "Birth weight (g)", (298, 714, 430, 737),
          ValueType.INT, unit="g", plausible=(300, 6500)),
    _text(P, "newborn", "newborn.head_circumference", "Périmètre crânien (cm)", "Head circumference (cm)",
          (342, 734, 460, 757), ValueType.FLOAT, unit="cm", plausible=(20, 45)),
    _text(P, "newborn", "newborn.anomaly", "Anomalie", "Abnormality", (296, 756, 560, 779),
          vocabulary="history"),
    _text(P, "newborn", "newborn.gest_age", "Âge gestationnel (SA)", "Gestational age (weeks)",
          (285, 776, 420, 800), ValueType.GEST_AGE, plausible=(20, 45)),
]


# ---------------------------------------------------------------------------
# Pages 5 / 7 — postpartum consultation, mother (early / late share the layout)
# ---------------------------------------------------------------------------
def _pp_mother(P: PageType, early: bool) -> list[FieldSpec]:
    t1, t2 = (("7-8 jours", "Day 7-8"), ("> 8 jours", "> day 8")) if early else \
        (("40-50 jours", "Day 40-50"), ("> 50 jours", "> day 50"))
    return [
        *_group(P, "consultation", "timing", True, [("window", t1[0], t1[1], 251, 75),
                                                    ("after", t2[0], t2[1], 251, 89)]),
        _text(P, "consultation", "consultation_date", "Date de la consultation", "Consultation date",
              (420, 65, 560, 89), ValueType.DATE),
        _text(P, "vitals", "temperature", "Température (°C)", "Temperature (°C)", (58, 126, 148, 150),
              ValueType.FLOAT, unit="°C", plausible=(34, 42.5)),
        _text(P, "vitals", "bp", "Tension artérielle", "Blood pressure", (164, 126, 258, 150), ValueType.BP,
              unit="mmHg"),
        _text(P, "vitals", "pulse", "Pouls", "Pulse", (284, 126, 358, 150), ValueType.INT, unit="bpm",
              plausible=(35, 180)),
        _text(P, "vitals", "weight", "Poids (kg)", "Weight (kg)", (384, 126, 480, 150), ValueType.FLOAT,
              unit="kg", plausible=(30, 150)),
        *_group(P, "exam", "conjunctiva", True, [("normal", "Conjonctives normales", "Normal conjunctiva", 161, 155),
                                                 ("pale", "Conjonctives décolorées", "Pale conjunctiva", 251, 155)]),
        _box(P, "exam", "uterine_globe", "Globe utérin présent", "Uterine globe present", 47, 172),
        *_group(P, "exam", "lochia", False, [
            ("odorless", "Lochies fades", "Odourless lochia", 161, 191),
            ("foul", "Lochies fétides", "Foul lochia", 231, 191),
            ("clear", "Lochies claires", "Clear lochia", 161, 207),
            ("bloody", "Lochies sanglantes", "Bloody lochia", 231, 207),
            ("yellowish", "Lochies jaunâtres", "Yellowish lochia", 311, 207)]),
        *_group(P, "exam", "perineum", False, [
            ("normal", "Périnée normal", "Normal perineum", 60, 242),
            ("episiotomy", "Épisiotomie", "Episiotomy", 60, 258),
            ("tear", "Déchirure", "Tear", 60, 274),
            ("repaired", "Réparée", "Repaired", 200, 259)]),
        *_group(P, "exam", "sphincters", True, [("normal", "Sphincters normaux", "Normal sphincters", 230, 297),
                                                ("abnormal", "Sphincters anormaux", "Abnormal sphincters", 300, 297)]),
        _box(P, "exam", "cesarean", "Césarienne", "Cesarean", 46, 316),
        _text(P, "exam", "scar_state", "État de la cicatrice", "Scar condition", (252, 309, 560, 331)),
        *_group(P, "exam", "breasts", False, [
            ("normal", "Seins normaux", "Normal breasts", 130, 337),
            ("lymphangitis", "Lymphangite", "Lymphangitis", 200, 337),
            ("mastitis", "Mastite et abcès", "Mastitis / abscess", 290, 337)]),
        *_group(P, "exam", "calves", False, [
            ("normal", "Mollets normaux", "Normal calves", 60, 372), ("red", "Mollets rouges", "Red calves", 130, 373),
            ("warm", "Mollets chauds", "Warm calves", 200, 373),
            ("painful", "Douloureux à la dorsiflexion", "Painful on dorsiflexion", 270, 373)]),
        _box(P, "complications", "complications.present", "Présence de complication", "Complication present", 190, 397),
        *_group(P, "complications", "complication_type", False, [
            ("hemorrhage", "Hémorragie", "Haemorrhage", 60, 414), ("infection", "Infection", "Infection", 60, 429),
            ("eclampsia", "Éclampsie", "Eclampsia", 60, 444), ("phlebitis", "Phlébite", "Phlebitis", 60, 459),
            ("breast", "Complications mammaires", "Breast complications", 220, 415),
            ("anemia", "Anémie", "Anaemia", 220, 430), ("other", "Autres", "Other", 220, 445)]),
        _box(P, "treatment", "medication_intake", "Prise de médicaments", "Taking medication", 190, 481),
        _text(P, "treatment", "medication_details", "Médicaments pris", "Medication details", (204, 473, 560, 497)),
        *_group(P, "treatment", "prescribed", False, [("iron", "Fer", "Iron", 60, 532),
                                                      ("vitamin_a", "Vitamine A", "Vitamin A", 140, 533)]),
        _text(P, "treatment", "treatment_other", "Autre traitement", "Other treatment", (118, 545, 560, 566)),
        _text(P, "treatment", "next_appointment", "Prochain rendez-vous", "Next appointment", (135, 559, 320, 581),
              ValueType.DATE),
        _box(P, "family_planning", "fp.wants_method", "Désire une méthode contraceptive", "Wants contraception", 45, 624),
        *_group(P, "family_planning", "fp_method", False, [("pill", "Pilule", "Pill", 139, 643),
                                                           ("iud", "DIU", "IUD", 209, 643)]),
        _text(P, "family_planning", "fp.other_method", "Autre méthode", "Other method", (350, 635, 560, 657)),
        _box(P, "family_planning", "fp.prescribed", "Prescription faite", "Prescription given", 45, 660),
        _box(P, "family_planning", "fp.referred", "Référée", "Referred", 45, 676),
        _text(P, "family_planning", "fp.refusal_reason", "Raison du refus de contraception",
              "Reason for declining contraception", (44, 706, 560, 731)),
    ]


# ---------------------------------------------------------------------------
# Pages 6 / 8 — postpartum consultation, newborn
# ---------------------------------------------------------------------------
def _pp_newborn(P: PageType) -> list[FieldSpec]:
    return [
        _text(P, "consultation", "consultation_date", "Date de la consultation", "Consultation date",
              (420, 65, 560, 89), ValueType.DATE),
        _text(P, "vitals", "age_days", "Âge (jours)", "Age (days)", (66, 99, 186, 123), ValueType.INT,
              unit="jours", plausible=(0, 120)),
        _text(P, "vitals", "temperature", "Température (°C)", "Temperature (°C)", (240, 99, 376, 123),
              ValueType.FLOAT, unit="°C", plausible=(33, 42.5)),
        _text(P, "vitals", "weight", "Poids (g)", "Weight (g)", (405, 99, 560, 123), ValueType.INT, unit="g",
              plausible=(500, 9000)),
        _text(P, "vitals", "length", "Taille (cm)", "Length (cm)", (70, 121, 186, 145), ValueType.FLOAT, unit="cm",
              plausible=(30, 75)),
        _text(P, "vitals", "head_circumference", "Périmètre crânien (cm)", "Head circumference (cm)",
              (258, 121, 376, 145), ValueType.FLOAT, unit="cm", plausible=(20, 50)),
        _box(P, "status", "premature", "Nouveau-né prématuré", "Preterm newborn", 47, 152),
        _box(P, "status", "hypotrophic", "Nouveau-né hypotrophe", "Small for gestational age", 191, 152),
        *_group(P, "feeding", "feeding", True, [
            ("exclusive_breastfeeding", "Allaitement exclusif au sein", "Exclusive breastfeeding", 111, 174),
            ("formula", "Allaitement artificiel", "Formula feeding", 251, 175),
            ("mixed", "Allaitement mixte", "Mixed feeding", 331, 175)]),
        *_group(P, "danger_signs", "danger_signs", False, [
            ("convulsions", "Convulsions", "Convulsions", 51, 216),
            ("feeding_refusal", "Refus de téter", "Not feeding", 176, 216),
            ("hematemesis", "Hématémèses", "Haematemesis", 301, 217),
            ("melena", "Méléna", "Melaena", 426, 218),
            ("diarrhea", "Diarrhée", "Diarrhoea", 51, 234), ("jaundice", "Ictère", "Jaundice", 176, 234),
            ("chest_indrawing", "Tirage sous-costal", "Chest indrawing", 301, 235),
            ("cough", "Toux", "Cough", 426, 236),
            ("abnormal_breathing", "Rythme respiratoire anormal", "Abnormal breathing", 51, 252),
            ("fever", "Fièvre", "Fever", 176, 252), ("hypothermia", "Hypothermie", "Hypothermia", 301, 253)]),
        _text(P, "danger_signs", "danger_signs_other", "Autres signes de danger", "Other danger signs",
              (115, 268, 560, 292), vocabulary="history"),
        *_group(P, "trauma", "trauma", False, [
            ("cephalhematoma", "Bosse séro-sanguine / céphalhématome", "Cephalhaematoma", 60, 324),
            ("hip_dislocation", "Luxation congénitale de la hanche", "Congenital hip dislocation", 60, 340),
            ("limb_mobility", "Mobilité d'un membre diminuée", "Reduced limb mobility", 60, 356)]),
        _text(P, "trauma", "trauma_other", "Autres lésions / malformations", "Other injuries / malformations",
              (128, 371, 560, 395), vocabulary="history"),
        *_group(P, "feeding", "breastfeeding_eval", True, [
            ("normal", "Allaitement normal", "Breastfeeding normal", 250, 405),
            ("problems", "Allaitement à problèmes", "Breastfeeding problems", 320, 405)]),
        *_group(P, "vaccination", "vaccines_today", False, [("BCG", "BCG", "BCG", 190, 442),
                                                            ("HB", "Hépatite B", "Hepatitis B", 250, 443)]),
        _box(P, "vaccination", "vitamin_d", "Supplémentation en vitamine D", "Vitamin D supplementation", 46, 462),
        *_group(P, "complications", "complications", False, [
            ("jaundice", "Ictère", "Jaundice", 60, 506), ("infection", "Infection", "Infection", 140, 506),
            ("conjunctivitis", "Conjonctivite", "Conjunctivitis", 220, 507),
            ("trauma", "Traumatisme", "Trauma", 59, 524), ("malformation", "Malformation", "Malformation", 139, 524),
            ("other", "Autres", "Other", 59, 542)]),
        _text(P, "care", "seen_by", "Vu par", "Seen by", (76, 573, 400, 597)),
        _text(P, "care", "decision", "Décision prise", "Decision", (103, 603, 560, 628)),
        _text(P, "care", "treatment", "Traitement prescrit", "Treatment prescribed", (119, 633, 560, 658),
              vocabulary="history"),
        _box(P, "care", "transfer", "Transfert", "Transfer", 45, 677),
        _text(P, "care", "referral_facility", "Établissement de référence", "Referral facility",
              (258, 669, 560, 693)),
        _text(P, "care", "follow_up_date", "Visite de suivi le", "Follow-up visit on", (208, 754, 400, 778),
              ValueType.DATE),
    ]


PAGE_FIELDS: dict[PageType, list[FieldSpec]] = {
    PageType.COVER: COVER,
    PageType.HISTORY: HISTORY,
    PageType.PREGNANCY: PREGNANCY,
    PageType.DELIVERY: DELIVERY,
    PageType.PP_EARLY_MOTHER: _pp_mother(PageType.PP_EARLY_MOTHER, early=True),
    PageType.PP_EARLY_NEWBORN: _pp_newborn(PageType.PP_EARLY_NEWBORN),
    PageType.PP_LATE_MOTHER: _pp_mother(PageType.PP_LATE_MOTHER, early=False),
    PageType.PP_LATE_NEWBORN: _pp_newborn(PageType.PP_LATE_NEWBORN),
}

ALL_FIELDS: dict[str, FieldSpec] = {f.id: f for fields in PAGE_FIELDS.values() for f in fields}

# Position of each page type in the booklet (the specimen PDF repeats this order for every patient).
PAGE_ORDER: list[PageType] = list(PAGE_FIELDS)

# Direct identifiers visible on paper: redacted from every stored image, never extracted.
PII_ZONES: dict[PageType, list[tuple[str, Rect]]] = {
    PageType.COVER: [("patient_name", (165, 365, 565, 393))],
    PageType.HISTORY: [("national_id", (325, 74, 565, 98)), ("address", (88, 117, 298, 141)),
                       ("phone", (348, 119, 565, 143)), ("husband_name", (104, 139, 298, 163))],
    PageType.DELIVERY: [("patient_name", (75, 69, 420, 95))],
    PageType.PP_EARLY_MOTHER: [("patient_name", (38, 44, 330, 61))],
    PageType.PP_LATE_MOTHER: [("patient_name", (38, 44, 330, 61))],
    PageType.PP_EARLY_NEWBORN: [],
    PageType.PP_LATE_NEWBORN: [],
}

# Sanity: field ids must be unique.
assert len(ALL_FIELDS) == sum(len(v) for v in PAGE_FIELDS.values()), "duplicate field ids in layout"
