#!/usr/bin/env python3
"""Collect free supplementary datasets for the Hormuz trade-reorganization paper."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path("output/supplementary")
RAW = OUT / "raw"
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(exist_ok=True)

PORTWATCH_URL = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
PORT_DICTIONARY_URL = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/PortWatch_chokepoints_database/FeatureServer/0/query"
PINK_URL = "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
GEODIST_URL = "https://www.cepii.fr/distance/dist_cepii.dta"
SELECTED_PORTS = ["chokepoint1", "chokepoint2", "chokepoint4", "chokepoint5", "chokepoint6", "chokepoint7", "chokepoint8", "chokepoint9"]
EU27_ALPHA3 = {"AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE"}


def request_json(session: requests.Session, url: str, params: dict, timeout: int = 180) -> dict:
    for attempt in range(7):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload
        except Exception:
            if attempt == 6:
                raise
            time.sleep(min(60, 2 ** attempt))
    raise AssertionError


def collect_portwatch(session: requests.Session) -> dict:
    dictionary_payload = request_json(session, PORT_DICTIONARY_URL, {
        "where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json", "resultRecordCount": 1000,
    })
    dictionary = pd.DataFrame(feature["attributes"] for feature in dictionary_payload.get("features", []))
    id_column = next(column for column in dictionary.columns if column.lower() == "portid")
    name_column = next(column for column in dictionary.columns if column.lower() in {"portname", "name"})
    port_dictionary = dictionary[[id_column, name_column]].rename(columns={id_column: "portid", name_column: "portname"})
    port_dictionary["portid"] = port_dictionary["portid"].astype(str).str.lower()
    port_dictionary.to_csv(OUT / "portwatch_chokepoint_dictionary.csv", index=False)

    where = " OR ".join(f"portid='{port}'" for port in SELECTED_PORTS)
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        payload = request_json(session, PORTWATCH_URL, {
            "where": where, "outFields": "*", "returnGeometry": "false", "f": "json",
            "resultOffset": offset, "resultRecordCount": page_size, "orderByFields": "portid,year,month,day,ObjectId",
        })
        batch = [feature["attributes"] for feature in payload.get("features", [])]
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    daily = pd.DataFrame(rows)
    daily.columns = [str(column).strip() for column in daily.columns]
    daily["portid"] = daily["portid"].astype(str).str.lower()
    # PortWatch currently carries reliable year/month/day components even when the
    # ArcGIS date field is null.
    daily["date"] = pd.to_datetime({
        "year": pd.to_numeric(daily["year"], errors="coerce"),
        "month": pd.to_numeric(daily["month"], errors="coerce"),
        "day": pd.to_numeric(daily["day"], errors="coerce"),
    }, errors="coerce")
    numeric = [column for column in daily.columns if column.startswith("n_") or column.startswith("capacity")]
    for column in numeric:
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.drop(columns=[column for column in ["portname"] if column in daily.columns]).merge(
        port_dictionary, on="portid", how="left", validate="many_to_one"
    )
    daily = daily.sort_values(["portid", "date"]).drop_duplicates(["portid", "date"], keep="last").reset_index(drop=True)
    daily.to_csv(OUT / "portwatch_daily_chokepoints_2019_2026.csv.gz", index=False, compression="gzip")

    baseline = daily[daily["date"].between("2025-02-28", "2026-02-27")].groupby("portid", as_index=False).agg(
        baseline_total_mean=("n_total", "mean"),
        baseline_total_median=("n_total", "median"),
        baseline_tanker_mean=("n_tanker", "mean"),
        baseline_cargo_mean=("n_cargo", "mean"),
        baseline_capacity_mean=("capacity", "mean"),
        baseline_days=("date", "nunique"),
    )
    enriched = daily.merge(baseline, on="portid", how="left", validate="many_to_one")
    enriched["relative_total"] = enriched["n_total"] / enriched["baseline_total_mean"]
    enriched["relative_tanker"] = enriched["n_tanker"] / enriched["baseline_tanker_mean"]
    enriched["relative_capacity"] = enriched["capacity"] / enriched["baseline_capacity_mean"]
    enriched.to_csv(OUT / "portwatch_daily_chokepoints_with_baselines.csv.gz", index=False, compression="gzip")
    return {
        "status": "ok", "rows": int(len(daily)), "chokepoints": int(daily["portid"].nunique()),
        "first_date": str(daily["date"].min().date()), "latest_date": str(daily["date"].max().date()),
        "selected": port_dictionary[port_dictionary["portid"].isin(SELECTED_PORTS)].to_dict("records"),
    }


def parse_pink_month(value: object) -> pd.Timestamp:
    text = str(value).strip()
    match = re.fullmatch(r"(\d{4})M(\d{2})", text)
    if not match:
        return pd.NaT
    return pd.Timestamp(int(match.group(1)), int(match.group(2)), 1)


def collect_pink_sheet(session: requests.Session) -> dict:
    destination = RAW / "CMO-Historical-Data-Monthly.xlsx"
    response = session.get(PINK_URL, timeout=300)
    response.raise_for_status()
    destination.write_bytes(response.content)
    workbook = pd.ExcelFile(destination)
    raw = pd.read_excel(destination, sheet_name="Monthly Prices", header=None)
    # July 2026 workbook: series names row 4, units row 5, data from row 6.
    names = raw.iloc[4].copy()
    units = raw.iloc[5].copy()
    data = raw.iloc[6:].copy()
    data = data.rename(columns={data.columns[0]: "month"})
    data["month"] = data["month"].map(parse_pink_month)
    data = data.dropna(subset=["month"])
    column_records = []
    renames = {}
    used = set()
    for column in data.columns[1:]:
        name = str(names.get(column, "")).strip()
        if not name or name.lower() == "nan":
            name = f"series_{column}"
        unique = name
        suffix = 2
        while unique in used:
            unique = f"{name}_{suffix}"
            suffix += 1
        used.add(unique)
        renames[column] = unique
        column_records.append({"series": unique, "commodity_name": name, "unit": str(units.get(column, "")).strip(), "provider_code": unique})
    data = data.rename(columns=renames)
    for column in data.columns[1:]:
        data[column] = pd.to_numeric(data[column].replace({"…": np.nan, "..": np.nan, "": np.nan}), errors="coerce")
    data.to_csv(OUT / "world_bank_pink_sheet_monthly_wide.csv.gz", index=False, compression="gzip")
    long = data.melt(id_vars="month", var_name="series", value_name="value")
    metadata = pd.DataFrame(column_records)
    long = long.merge(metadata, on="series", how="left", validate="many_to_one")
    long.to_csv(OUT / "world_bank_pink_sheet_monthly_long.csv.gz", index=False, compression="gzip")
    return {
        "status": "ok", "url": PINK_URL, "bytes": destination.stat().st_size,
        "first_month": str(data["month"].min().date()), "latest_month": str(data["month"].max().date()),
        "sheet_names": workbook.sheet_names, "rows": int(len(data)), "series": int(len(data.columns) - 1),
    }


def collect_geodist(session: requests.Session) -> dict:
    destination = RAW / "dist_cepii.dta"
    response = session.get(GEODIST_URL, timeout=300)
    response.raise_for_status()
    destination.write_bytes(response.content)
    frame = pd.read_stata(destination)
    frame.to_csv(OUT / "cepii_geodist_full.csv.gz", index=False, compression="gzip")
    eu = frame[frame["iso_d"].isin(EU27_ALPHA3)].copy()
    eu.to_csv(OUT / "cepii_geodist_eu_pairs.csv.gz", index=False, compression="gzip")
    missing_destinations = sorted(EU27_ALPHA3 - set(frame["iso_d"].dropna().unique()))
    return {
        "status": "ok", "url": GEODIST_URL, "bytes": destination.stat().st_size,
        "rows": int(len(frame)), "eu_rows": int(len(eu)), "columns": frame.columns.tolist(),
        "missing_eu_destinations": missing_destinations,
    }


def main() -> None:
    audit = {}
    with requests.Session() as session:
        session.headers.update({"User-Agent": "academic-hormuz-energy-paper/1.0"})
        for name, function in [("portwatch", collect_portwatch), ("pink_sheet", collect_pink_sheet), ("geodist", collect_geodist)]:
            try:
                audit[name] = function(session)
            except Exception as exc:
                audit[name] = {"status": "failed", "error": repr(exc)}
    audit["retrieved_at_utc"] = pd.Timestamp.utcnow().isoformat()
    (OUT / "supplementary_build_audit.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, indent=2, default=str), flush=True)
    if any(record.get("status") != "ok" for key, record in audit.items() if isinstance(record, dict) and key != "retrieved_at_utc"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
