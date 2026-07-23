#!/usr/bin/env python3
"""Build one year of monthly EU import data from official Eurostat Comext archives.

The current Comext PRODUCTS directory exposes full monthly archives from 2002.
This collector downloads the 12 monthly full_v2 archives (or available months),
retains current-EU27 reporters, import flows and CN chapters 28, 31 and 76,
aggregates statistical-procedure rows to reporter-origin-CN8-month, and deletes
large raw files immediately after processing.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import py7zr
import requests

FILES_ENDPOINT = "https://ec.europa.eu/eurostat/api/dissemination/files"
PRODUCT_DIR = "comext/COMEXT_DATA/PRODUCTS"
EU27 = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE",
}
CHAPTERS = {"28", "31", "76"}
USECOLS = ["REPORTER", "PARTNER", "PRODUCT_NC", "FLOW", "PERIOD", "VALUE_EUR", "QUANTITY_KG"]


def get(session: requests.Session, params: dict, *, stream: bool = False, timeout: int = 900) -> requests.Response:
    last: Exception | None = None
    for attempt in range(7):
        try:
            response = session.get(FILES_ENDPOINT, params=params, stream=stream, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = exc
            if attempt == 6:
                raise
            time.sleep(min(90, 2 ** attempt))
    raise RuntimeError(last)


def directory_listing(session: requests.Session) -> pd.DataFrame:
    response = get(session, {
        "format": "csv", "dir": PRODUCT_DIR, "hierarchy": "false",
        "sizeFormat": "NONE", "dateFormat": "ISO", "sort": "name",
    }, timeout=180)
    listing = pd.read_csv(io.StringIO(response.text), dtype=str)
    listing.columns = [str(c).strip().upper() for c in listing.columns]
    return listing


def monthly_archives(listing: pd.DataFrame, year: int) -> list[str]:
    pattern = re.compile(fr"^full_v2_{year}(0[1-9]|1[0-2])\.7z$")
    names = [name for name in listing["NAME"].dropna().tolist() if pattern.match(name)]
    return sorted(names)


def download(session: requests.Session, name: str, destination: Path) -> int:
    response = get(session, {"file": f"{PRODUCT_DIR}/{name}"}, stream=True)
    with destination.open("wb") as handle:
        for chunk in response.iter_content(1024 * 1024):
            if chunk:
                handle.write(chunk)
    return destination.stat().st_size


def read_selected(path: Path, archive_name: str, year: int) -> tuple[pd.DataFrame, dict]:
    frames: list[pd.DataFrame] = []
    raw_rows = selected_rows = 0
    for chunk in pd.read_csv(path, usecols=USECOLS, dtype=str, chunksize=500_000, low_memory=False):
        raw_rows += len(chunk)
        reporter = chunk["REPORTER"].fillna("").str.strip().str.upper()
        partner = chunk["PARTNER"].fillna("").str.strip().str.upper()
        product = chunk["PRODUCT_NC"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(8)
        flow = chunk["FLOW"].fillna("").astype(str).str.strip().str.upper()
        period = chunk["PERIOD"].fillna("").astype(str).str.extract(r"(\d{6})", expand=False)
        import_flow = flow.isin({"1", "01", "I", "IMP", "IMPORT"}) | flow.str.startswith("1.")
        mask = (
            reporter.isin(EU27)
            & partner.str.fullmatch(r"[A-Z]{2}")
            & product.str[:2].isin(CHAPTERS)
            & import_flow
            & period.str[:4].eq(str(year))
            & period.str[4:6].isin([f"{m:02d}" for m in range(1, 13)])
        )
        if not mask.any():
            continue
        x = pd.DataFrame({
            "reporter": reporter[mask].values,
            "partner": partner[mask].values,
            "period": period[mask].values,
            "cn8": product[mask].values,
            "trade_value_eur": pd.to_numeric(chunk.loc[mask, "VALUE_EUR"], errors="coerce").values,
            "quantity_kg": pd.to_numeric(chunk.loc[mask, "QUANTITY_KG"], errors="coerce").values,
        })
        x["hs6"] = x["cn8"].str[:6]
        x["month"] = pd.to_datetime(x["period"] + "01", format="%Y%m%d", errors="coerce")
        x = x.drop(columns="period").dropna(subset=["month"])
        selected_rows += len(x)
        frames.append(x)
    if frames:
        data = pd.concat(frames, ignore_index=True)
        data["negative_value_flag"] = data["trade_value_eur"].lt(0).astype("int8")
        data["negative_quantity_flag"] = data["quantity_kg"].lt(0).astype("int8")
        data.loc[data["trade_value_eur"].lt(0), "trade_value_eur"] = np.nan
        data.loc[data["quantity_kg"].lt(0), "quantity_kg"] = np.nan
        data = data.groupby(["reporter", "partner", "month", "cn8", "hs6"], as_index=False, dropna=False).agg(
            trade_value_eur=("trade_value_eur", "sum"),
            quantity_kg=("quantity_kg", "sum"),
            raw_rows=("hs6", "size"),
            negative_value_flag=("negative_value_flag", "max"),
            negative_quantity_flag=("negative_quantity_flag", "max"),
        )
        data["source_archive"] = archive_name
    else:
        data = pd.DataFrame(columns=[
            "reporter", "partner", "month", "cn8", "hs6", "trade_value_eur",
            "quantity_kg", "raw_rows", "negative_value_flag",
            "negative_quantity_flag", "source_archive",
        ])
    audit = {
        "archive": archive_name, "raw_rows": raw_rows,
        "selected_rows_before_aggregation": selected_rows,
        "aggregated_rows": int(len(data)),
    }
    return data, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    work = args.out_dir / "work"
    work.mkdir(exist_ok=True)
    frames: list[pd.DataFrame] = []
    audit: list[dict] = []
    status = "ok"
    error: str | None = None

    try:
        with requests.Session() as session:
            session.headers.update({"User-Agent": "academic-hormuz-energy-paper/1.0"})
            listing = directory_listing(session)
            archives = monthly_archives(listing, args.year)
            if not archives:
                raise RuntimeError(f"No monthly full_v2 archives found for {args.year}")
            for number, name in enumerate(archives, 1):
                archive_path = work / name
                record = {"archive": name}
                try:
                    record["archive_bytes"] = download(session, name, archive_path)
                    extract_dir = work / f"extract_{number:02d}"
                    extract_dir.mkdir(exist_ok=True)
                    with py7zr.SevenZipFile(archive_path, mode="r") as seven:
                        record["contained_files"] = seven.getnames()
                        seven.extractall(extract_dir)
                    file_records = []
                    for data_file in [p for p in extract_dir.rglob("*") if p.is_file()]:
                        selected, file_audit = read_selected(data_file, name, args.year)
                        file_records.append(file_audit)
                        if not selected.empty:
                            frames.append(selected)
                    record["files"] = file_records
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    archive_path.unlink(missing_ok=True)
                except Exception as exc:
                    record["error"] = repr(exc)
                    archive_path.unlink(missing_ok=True)
                audit.append(record)
                print(json.dumps(record, default=str), flush=True)
    except Exception as exc:
        status = "failed"
        error = repr(exc)

    if frames:
        panel = pd.concat(frames, ignore_index=True)
        panel = panel.groupby(["reporter", "partner", "month", "cn8", "hs6"], as_index=False, dropna=False).agg(
            trade_value_eur=("trade_value_eur", "sum"),
            quantity_kg=("quantity_kg", "sum"),
            raw_rows=("raw_rows", "sum"),
            negative_value_flag=("negative_value_flag", "max"),
            negative_quantity_flag=("negative_quantity_flag", "max"),
            source_archives=("source_archive", lambda values: "|".join(sorted(set(values)))),
        )
        panel["unit_value_eur_per_kg"] = np.where(
            panel["quantity_kg"].gt(0), panel["trade_value_eur"] / panel["quantity_kg"], np.nan
        )
        panel = panel.sort_values(["month", "reporter", "partner", "cn8"]).reset_index(drop=True)
    else:
        panel = pd.DataFrame(columns=[
            "reporter", "partner", "month", "cn8", "hs6", "trade_value_eur",
            "quantity_kg", "raw_rows", "negative_value_flag",
            "negative_quantity_flag", "source_archives", "unit_value_eur_per_kg",
        ])

    output = args.out_dir / f"comext_eu27_imports_ch28_ch31_ch76_{args.year}.csv.gz"
    panel.to_csv(output, index=False, compression="gzip")
    summary = {
        "year": args.year, "status": status, "error": error,
        "archives_expected_or_available": len(audit),
        "archives_with_errors": [r["archive"] for r in audit if "error" in r],
        "rows": int(len(panel)),
        "reporters": int(panel["reporter"].nunique()) if len(panel) else 0,
        "partners": int(panel["partner"].nunique()) if len(panel) else 0,
        "months": int(panel["month"].nunique()) if len(panel) else 0,
        "cn8_products": int(panel["cn8"].nunique()) if len(panel) else 0,
        "hs6_products": int(panel["hs6"].nunique()) if len(panel) else 0,
        "first_month": str(panel["month"].min()) if len(panel) else None,
        "latest_month": str(panel["month"].max()) if len(panel) else None,
        "audit": audit,
    }
    (args.out_dir / f"audit_{args.year}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "audit"}, indent=2), flush=True)
    if status != "ok" or len(panel) == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
