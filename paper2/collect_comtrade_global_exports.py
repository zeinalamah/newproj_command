#!/usr/bin/env python3
"""Collect a free global monthly export panel from UN Comtrade preview API.

The public preview endpoint requires no key and returns at most 500 records. By
requesting one HS6 product and one month at a time, with partner World and no
reporter restriction, the full exporter cross-section fits below that cap.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

OUT = Path("output/comtrade_global_exports")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
PRODUCTS = {
    "281410": "anhydrous_ammonia",
    "281420": "aqueous_ammonia",
    "310210": "urea",
    "760110": "unwrought_aluminium_not_alloyed",
    "760120": "unwrought_aluminium_alloys",
}
MONTHS = [str(period) for period in pd.period_range("2019-01", "2026-06", freq="M")]


def fetch(session: requests.Session, product: str, month: str) -> tuple[dict, dict]:
    params = {
        "period": month.replace("-", ""),
        "cmdCode": product,
        "flowCode": "X",
        "partnerCode": "0",
        "partner2Code": "0",
        "maxRecords": "500",
        "includeDesc": "false",
        "breakdownMode": "classic",
    }
    last = None
    for attempt in range(10):
        try:
            response = session.get(BASE, params=params, timeout=180)
            if response.status_code == 429:
                delay = float(response.headers.get("Retry-After", 1.5)) + 0.5
                time.sleep(max(delay, 1.5) * (1 + attempt / 5))
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            record = {
                "product": product, "month": month, "status": "ok",
                "count": int(payload.get("count", 0)),
                "returned": len(payload.get("data", [])),
                "elapsed_time": payload.get("elapsedTime"),
                "truncated_at_500": int(payload.get("count", 0)) >= 500,
                "url": response.url,
            }
            return payload, record
        except Exception as exc:
            last = exc
            if attempt == 9:
                return {"data": []}, {"product": product, "month": month, "status": "failed", "error": repr(exc), "url": response.url if 'response' in locals() else None}
            time.sleep(min(60, 2 ** attempt))
    raise RuntimeError(last)


def main() -> None:
    frames = []
    audit = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "academic-hormuz-energy-paper/1.0"})
        total = len(PRODUCTS) * len(MONTHS)
        completed = 0
        for product, label in PRODUCTS.items():
            for month in MONTHS:
                payload, record = fetch(session, product, month)
                data = pd.DataFrame(payload.get("data", []))
                if not data.empty:
                    data["product_label"] = label
                    frames.append(data)
                audit.append(record)
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(json.dumps({"completed": completed, "total": total, "failures": sum(r["status"] != "ok" for r in audit), "truncated": sum(bool(r.get("truncated_at_500")) for r in audit)}), flush=True)
                time.sleep(1.25)

    audit_frame = pd.DataFrame(audit)
    audit_frame.to_csv(OUT / "comtrade_global_export_request_audit.csv", index=False)
    if not frames:
        raise RuntimeError("UN Comtrade returned no observations")
    data = pd.concat(frames, ignore_index=True)
    keep = [
        "period", "refYear", "refMonth", "reporterCode", "reporterISO", "reporterDesc",
        "flowCode", "partnerCode", "partnerISO", "classificationCode",
        "isOriginalClassification", "cmdCode", "cmdDesc", "qty", "netWgt",
        "fobvalue", "primaryValue", "isReported", "isAggregate", "product_label",
    ]
    for column in keep:
        if column not in data:
            data[column] = pd.NA
    data = data[keep].copy()
    data["month"] = pd.to_datetime(data["period"].astype(str) + "01", format="%Y%m%d", errors="coerce")
    data["quantity_kg"] = pd.to_numeric(data["netWgt"], errors="coerce")
    data["quantity_kg"] = data["quantity_kg"].where(data["quantity_kg"].gt(0), pd.to_numeric(data["qty"], errors="coerce"))
    data["export_value_usd"] = pd.to_numeric(data["primaryValue"], errors="coerce")
    data["export_value_usd"] = data["export_value_usd"].where(data["export_value_usd"].notna(), pd.to_numeric(data["fobvalue"], errors="coerce"))
    data["unit_value_usd_per_kg"] = data["export_value_usd"] / data["quantity_kg"].where(data["quantity_kg"].gt(0))
    data["cmdCode"] = data["cmdCode"].astype(str).str.zfill(6)
    data = data.sort_values(["cmdCode", "month", "reporterISO"]).drop_duplicates(["cmdCode", "month", "reporterCode"], keep="last")
    data.to_csv(OUT / "un_comtrade_global_monthly_exports_2019_2026.csv.gz", index=False, compression="gzip")

    coverage = data.groupby(["cmdCode", "product_label"], as_index=False).agg(
        observations=("reporterISO", "size"),
        exporters=("reporterISO", "nunique"),
        first_month=("month", "min"),
        latest_month=("month", "max"),
        total_quantity_kg=("quantity_kg", "sum"),
        total_export_value_usd=("export_value_usd", "sum"),
    )
    coverage.to_csv(OUT / "comtrade_global_export_coverage.csv", index=False)
    summary = {
        "source": "UN Comtrade public preview API",
        "calls": len(audit_frame),
        "successful_calls": int(audit_frame["status"].eq("ok").sum()),
        "failed_calls": int(audit_frame["status"].ne("ok").sum()),
        "truncated_calls": int(audit_frame.get("truncated_at_500", pd.Series(dtype=bool)).fillna(False).sum()),
        "rows": int(len(data)),
        "exporters": int(data["reporterISO"].nunique()),
        "products": int(data["cmdCode"].nunique()),
        "first_month": str(data["month"].min().date()),
        "latest_month": str(data["month"].max().date()),
        "coverage": coverage.to_dict("records"),
    }
    (OUT / "comtrade_global_export_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str), flush=True)
    if summary["failed_calls"] > 0 or summary["truncated_calls"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
