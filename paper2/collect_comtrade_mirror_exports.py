#!/usr/bin/env python3
"""Recover contemporaneous affected-origin exports from global mirror imports.

Queries every reporting importer's monthly imports from the seven Hormuz-route
origins. The free public preview API needs no key. One product-month query stays
well below the 500-row cap while returning importer-origin bilateral records.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path("output/comtrade_mirror_exports")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
PRODUCTS = {
    "281410": "anhydrous_ammonia",
    "281420": "aqueous_ammonia",
    "310210": "urea",
    "760110": "unwrought_aluminium_not_alloyed",
    "760120": "unwrought_aluminium_alloys",
}
# Bahrain, Iraq, Iran, Kuwait, Qatar, Saudi Arabia, UAE. Oman is outside Hormuz.
AFFECTED_PARTNER_CODES = "48,368,364,414,634,682,784"
MONTHS = [str(period) for period in pd.period_range("2019-01", "2026-06", freq="M")]


def fetch(session: requests.Session, product: str, month: str) -> tuple[dict, dict]:
    params = {
        "period": month.replace("-", ""),
        "cmdCode": product,
        "flowCode": "M",
        "partnerCode": AFFECTED_PARTNER_CODES,
        "partner2Code": "0",
        "maxRecords": "500",
        "includeDesc": "true",
        "breakdownMode": "classic",
    }
    for attempt in range(10):
        try:
            response = session.get(BASE, params=params, timeout=180)
            if response.status_code == 429:
                time.sleep((2 + attempt) * 1.5)
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            record = {
                "product": product, "month": month, "status": "ok",
                "count": int(payload.get("count", 0)),
                "returned": len(payload.get("data", [])),
                "truncated_at_500": int(payload.get("count", 0)) >= 500,
                "elapsed_time": payload.get("elapsedTime"),
                "url": response.url,
            }
            return payload, record
        except Exception as exc:
            if attempt == 9:
                return {"data": []}, {"product": product, "month": month, "status": "failed", "error": repr(exc)}
            time.sleep(min(60, 2 ** attempt))
    raise AssertionError


def main() -> None:
    frames = []
    audit = []
    total = len(PRODUCTS) * len(MONTHS)
    completed = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "academic-hormuz-energy-paper/1.0"})
        for product, label in PRODUCTS.items():
            for month in MONTHS:
                payload, record = fetch(session, product, month)
                frame = pd.DataFrame(payload.get("data", []))
                if not frame.empty:
                    frame["product_label"] = label
                    frames.append(frame)
                audit.append(record)
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(json.dumps({
                        "completed": completed, "total": total,
                        "failures": sum(item["status"] != "ok" for item in audit),
                        "truncated": sum(bool(item.get("truncated_at_500")) for item in audit),
                    }), flush=True)
                time.sleep(1.25)

    audit_frame = pd.DataFrame(audit)
    audit_frame.to_csv(OUT / "comtrade_mirror_request_audit.csv", index=False)
    if not frames:
        raise RuntimeError("No mirror-import observations returned")
    data = pd.concat(frames, ignore_index=True)
    keep = [
        "period", "refYear", "refMonth", "reporterCode", "reporterISO", "reporterDesc",
        "flowCode", "partnerCode", "partnerISO", "partnerDesc", "classificationCode",
        "isOriginalClassification", "cmdCode", "cmdDesc", "qty", "netWgt",
        "cifvalue", "fobvalue", "primaryValue", "isQtyEstimated", "isNetWgtEstimated",
        "isReported", "isAggregate", "legacyEstimationFlag", "product_label",
    ]
    for column in keep:
        if column not in data:
            data[column] = pd.NA
    data = data[keep].copy()
    data["month"] = pd.to_datetime(data["period"].astype(str) + "01", format="%Y%m%d", errors="coerce")
    data["quantity_kg"] = pd.to_numeric(data["netWgt"], errors="coerce")
    data["quantity_kg"] = data["quantity_kg"].where(data["quantity_kg"].gt(0), pd.to_numeric(data["qty"], errors="coerce"))
    data["mirror_import_value_usd"] = pd.to_numeric(data["primaryValue"], errors="coerce")
    data["mirror_import_unit_value_usd_per_kg"] = data["mirror_import_value_usd"] / data["quantity_kg"].where(data["quantity_kg"].gt(0))
    data["cmdCode"] = data["cmdCode"].astype(str).str.zfill(6)
    data["reported_flag"] = data["isReported"].fillna(False).astype(bool).astype("int8")
    data["aggregate_or_estimated_flag"] = (
        data["isAggregate"].fillna(False).astype(bool)
        | data["isQtyEstimated"].fillna(False).astype(bool)
        | data["isNetWgtEstimated"].fillna(False).astype(bool)
    ).astype("int8")
    data = data.sort_values(["cmdCode", "month", "partnerCode", "reporterCode"]).drop_duplicates(
        ["cmdCode", "month", "partnerCode", "reporterCode"], keep="last"
    )
    data.to_csv(OUT / "un_comtrade_global_mirror_exports_2019_2026.csv.gz", index=False, compression="gzip")

    monthly = data.groupby(["cmdCode", "product_label", "partnerCode", "partnerISO", "partnerDesc", "month"], as_index=False).agg(
        mirror_export_quantity_kg=("quantity_kg", "sum"),
        mirror_export_value_usd=("mirror_import_value_usd", "sum"),
        importing_reporters=("reporterCode", "nunique"),
        reported_rows=("reported_flag", "sum"),
        total_rows=("reporterCode", "size"),
        estimated_rows=("aggregate_or_estimated_flag", "sum"),
    )
    monthly["mirror_export_unit_value_usd_per_kg"] = monthly["mirror_export_value_usd"] / monthly["mirror_export_quantity_kg"].where(monthly["mirror_export_quantity_kg"].gt(0))
    monthly.to_csv(OUT / "un_comtrade_affected_origin_monthly_mirror_exports_2019_2026.csv.gz", index=False, compression="gzip")

    coverage = monthly.groupby(["cmdCode", "product_label", "partnerISO", "partnerDesc"], dropna=False, as_index=False).agg(
        months=("month", "nunique"),
        first_month=("month", "min"),
        latest_month=("month", "max"),
        total_quantity_kg=("mirror_export_quantity_kg", "sum"),
        total_value_usd=("mirror_export_value_usd", "sum"),
        importing_reporters=("importing_reporters", "max"),
    )
    coverage.to_csv(OUT / "comtrade_mirror_export_coverage.csv", index=False)
    summary = {
        "source": "UN Comtrade public preview API, global mirror imports",
        "calls": len(audit_frame),
        "successful_calls": int(audit_frame["status"].eq("ok").sum()),
        "failed_calls": int(audit_frame["status"].ne("ok").sum()),
        "truncated_calls": int(audit_frame.get("truncated_at_500", pd.Series(dtype=bool)).fillna(False).sum()),
        "bilateral_rows": int(len(data)),
        "origin_product_month_rows": int(len(monthly)),
        "importers": int(data["reporterCode"].nunique()),
        "affected_origins": int(data["partnerCode"].nunique()),
        "products": int(data["cmdCode"].nunique()),
        "first_month": str(data["month"].min().date()),
        "latest_month": str(data["month"].max().date()),
        "coverage": coverage.to_dict("records"),
    }
    (OUT / "comtrade_mirror_export_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)
    if summary["failed_calls"] or summary["truncated_calls"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
