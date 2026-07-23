#!/usr/bin/env python3
"""Collect monthly Eurostat PPI and industrial-production data for exposed sectors."""
from __future__ import annotations

import itertools
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

OUT = Path("output/eurostat_sts")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
EU27 = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
NACE_CODES = ["C20", "C201", "C2013", "C2015", "C24", "C244", "C2442"]
QUERIES = [
    {"dataset": "sts_inpp_m", "indicator": "producer_price", "indic_bt": "PRC_PRR", "s_adj": "NSA", "unit": "I21"},
    {"dataset": "sts_inpr_m", "indicator": "industrial_production", "indic_bt": "PRD", "s_adj": "SCA", "unit": "I21"},
    {"dataset": "sts_inpr_m", "indicator": "industrial_production", "indic_bt": "PRD", "s_adj": "NSA", "unit": "I21"},
]


def ordered_codes(dimension: dict[str, Any]) -> list[str]:
    index = dimension.get("category", {}).get("index", {})
    if isinstance(index, list):
        return [str(value) for value in index]
    return [str(key) for key, _ in sorted(index.items(), key=lambda item: item[1])]


def fetch(session: requests.Session, dataset: str, parameters: dict[str, str]) -> dict[str, Any]:
    for attempt in range(7):
        try:
            response = session.get(f"{BASE}/{dataset}", params=parameters, timeout=300)
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise RuntimeError(payload["error"])
            if payload.get("warning", {}).get("status") == 413:
                raise RuntimeError(payload["warning"])
            return payload
        except Exception:
            if attempt == 6:
                raise
            time.sleep(min(60, 2 ** attempt))
    raise AssertionError


def parse_jsonstat(payload: dict[str, Any]) -> pd.DataFrame:
    dimensions = list(payload.get("id", []))
    sizes = [int(value) for value in payload.get("size", [])]
    if not dimensions or not sizes:
        return pd.DataFrame()
    categories = {dimension: ordered_codes(payload["dimension"][dimension]) for dimension in dimensions}
    values = payload.get("value", {})
    statuses = payload.get("status", {})
    iterator = enumerate(values) if isinstance(values, list) else ((int(key), value) for key, value in values.items())
    rows = []
    for flat_index, value in iterator:
        if value is None:
            continue
        coordinates = np.unravel_index(flat_index, tuple(sizes), order="C")
        row = {dimension: categories[dimension][position] for dimension, position in zip(dimensions, coordinates)}
        row["value"] = value
        if isinstance(statuses, list) and flat_index < len(statuses):
            row["status"] = statuses[flat_index]
        elif isinstance(statuses, dict):
            row["status"] = statuses.get(str(flat_index))
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    frames: list[pd.DataFrame] = []
    audit: list[dict[str, Any]] = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "academic-hormuz-energy-paper/1.0"})
        for query in QUERIES:
            for nace in NACE_CODES:
                params = {
                    "lang": "EN", "freq": "M", "indic_bt": query["indic_bt"],
                    "nace_r2": nace, "s_adj": query["s_adj"], "unit": query["unit"],
                    "sinceTimePeriod": "2000-01",
                }
                record = {**query, "nace_r2": nace, "parameters": params}
                try:
                    payload = fetch(session, query["dataset"], params)
                    frame = parse_jsonstat(payload)
                    if not frame.empty:
                        frame = frame[frame["geo"].isin(EU27)].copy()
                        frame["dataset"] = query["dataset"]
                        frame["indicator"] = query["indicator"]
                        frame["month"] = pd.to_datetime(frame["time"], errors="coerce")
                        frames.append(frame)
                    record.update({
                        "status": "ok", "updated": payload.get("updated"),
                        "raw_nonmissing": int(len(parse_jsonstat(payload))),
                        "eu_nonmissing": int(len(frame)),
                        "countries": int(frame["geo"].nunique()) if not frame.empty else 0,
                        "first_month": str(frame["month"].min().date()) if not frame.empty else None,
                        "latest_month": str(frame["month"].max().date()) if not frame.empty else None,
                    })
                except Exception as exc:
                    record.update({"status": "failed", "error": repr(exc)})
                audit.append(record)
                print(json.dumps(record, default=str), flush=True)
                time.sleep(0.1)
    if not frames:
        raise RuntimeError("No STS observations collected")
    data = pd.concat(frames, ignore_index=True)
    keep = ["dataset", "indicator", "freq", "indic_bt", "nace_r2", "s_adj", "unit", "geo", "month", "value", "status"]
    for column in keep:
        if column not in data:
            data[column] = np.nan
    data = data[keep].sort_values(["indicator", "nace_r2", "s_adj", "geo", "month"])
    data.to_csv(OUT / "eurostat_ppi_industrial_production_2000_2026.csv.gz", index=False, compression="gzip")
    coverage = data.groupby(["indicator", "nace_r2", "s_adj", "unit"], as_index=False).agg(
        observations=("value", "count"), countries=("geo", "nunique"),
        first_month=("month", "min"), latest_month=("month", "max"),
    )
    coverage.to_csv(OUT / "eurostat_sts_coverage.csv", index=False)
    pd.DataFrame(audit).to_json(OUT / "eurostat_sts_request_audit.json", orient="records", indent=2, date_format="iso")
    summary = {
        "rows": int(len(data)), "countries": int(data["geo"].nunique()),
        "nace_codes": sorted(data["nace_r2"].unique().tolist()),
        "first_month": str(data["month"].min().date()), "latest_month": str(data["month"].max().date()),
        "failed_queries": [record for record in audit if record.get("status") != "ok"],
    }
    (OUT / "eurostat_sts_build_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
