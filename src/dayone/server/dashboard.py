"""Anonymised aggregates for epidemiology (bonus): blood pressure, temperature, HIV / syphilis /
hepatitis tests.

Only counts and distributions leave this module, never a record. Any cell describing
fewer than ``k`` women is suppressed (shown as "<k"). Two sources are shown side by side:
the records registered through the agent, and the organisers' synthetic reference CSV
(200 pregnancies), which stands in for the historical registry.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import numpy as np

CSV_PATH = Path("data/maternal_registry_synthetic.csv")
SYS_BINS = [0, 90, 100, 110, 120, 130, 140, 160, 400]
DIA_BINS = [0, 60, 70, 80, 90, 100, 400]
TEMP_BINS = [0, 36.0, 36.5, 37.0, 37.5, 38.0, 45]


def _hist(values: list[float], bins: list[float], k: int) -> list[dict]:
    counts, _ = np.histogram(values, bins=bins)
    out = []
    for lo, hi, c in zip(bins[:-1], bins[1:], counts, strict=True):
        label = f"<{hi:g}" if lo == 0 else (f"≥{lo:g}" if hi >= 300 or hi == 45 else f"{lo:g}–{hi:g}")
        out.append({"bin": label, "count": int(c) if c >= k or c == 0 else None})
    return out


def _tests(counter: Counter, k: int) -> dict:
    total = sum(counter.values())
    out = {"tested": total if total >= k or total == 0 else None}
    for key in ("negative", "positive", "not_done"):
        c = counter.get(key, 0)
        out[key] = c if c >= k or c == 0 else None
    return out


def _from_records(records: list[dict], k: int) -> dict:
    sys_, dia, temp = [], [], []
    tests = {"hiv": Counter(), "syphilis": Counter(), "hbsag": Counter()}
    women = 0
    for rec in records:
        fields = rec.get("fields", {})
        women += 1
        for fid, e in fields.items():
            if e.get("status") != "KNOWN":
                continue
            v = e.get("value")
            name = fid.rsplit(".", 1)[-1]
            if name == "bp" and isinstance(v, str) and "/" in v:
                s, d = v.split("/")
                sys_.append(float(s))
                dia.append(float(d))
            elif name == "temperature" and isinstance(v, int | float) and "newborn" not in fid:
                temp.append(float(v))
            elif name in tests and isinstance(v, str):
                tests[name][v] += 1
    return {"women": women if women >= k or women == 0 else None,
            "n_bp": len(sys_), "systolic": _hist(sys_, SYS_BINS, k), "diastolic": _hist(dia, DIA_BINS, k),
            "mean_systolic": round(float(np.mean(sys_)), 1) if len(sys_) >= k else None,
            "mean_diastolic": round(float(np.mean(dia)), 1) if len(dia) >= k else None,
            "n_temperature": len(temp), "temperature": _hist(temp, TEMP_BINS, k),
            "tests": {name: _tests(c, k) for name, c in tests.items()}}


def _from_csv(path: Path, k: int) -> dict:
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open()))

    def col(name: str) -> list[float]:
        return [float(r[name]) for r in rows if r.get(name, "") != ""]

    sys_, dia = col("mean systolic bp (mmhg)"), col("mean diastolic bp (mmhg)")
    tests = {}
    for key, name in (("hiv", "hiv test result"), ("syphilis", "syphilis test result"),
                      ("hepatitis_c", "hepatitis c test result")):
        c = Counter()
        for r in rows:
            v = r.get(name, "")
            c["positive" if v == "1" else "negative" if v == "0" else "not_done"] += 1
        tests[key] = _tests(c, k)
    return {"women": len(rows), "n_bp": len(sys_), "systolic": _hist(sys_, SYS_BINS, k),
            "diastolic": _hist(dia, DIA_BINS, k), "mean_systolic": round(float(np.mean(sys_)), 1),
            "mean_diastolic": round(float(np.mean(dia)), 1), "tests": tests}


def aggregates(records: list[dict], k: int = 5, csv_path: Path = CSV_PATH) -> dict:
    return {"k_anonymity": k, "registered": _from_records(records, k), "reference_csv": _from_csv(csv_path, k)}
