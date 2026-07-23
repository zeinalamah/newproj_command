#!/usr/bin/env python3
"""Combine annual Comext artifacts into a publication-ready long panel."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

INPUT = Path("downloaded_years")
OUT = Path("output/comext_long_panel")
OUT.mkdir(parents=True, exist_ok=True)
FOCAL_HS6 = {
    "281410", "281420", "310210", "310230", "310240", "310260", "310280",
    "310290", "310520", "310530", "310540", "310551", "310559", "310560",
    "310590", "760110", "760120",
}

def files() -> list[Path]:
    return sorted(INPUT.rglob("comext_eu27_imports_ch28_ch31_ch76_*.csv.gz"))


def main() -> None:
    source_files = files()
    if not source_files:
        raise RuntimeError("No annual Comext CSV files found")
    writer: pq.ParquetWriter | None = None
    focal_frames: list[pd.DataFrame] = []
    summaries = []
    for path in source_files:
        frame = pd.read_csv(
            path, parse_dates=["month"], low_memory=False,
            dtype={"reporter": str, "partner": str, "cn8": str, "hs6": str},
        )
        frame["cn8"] = frame["cn8"].str.zfill(8)
        frame["hs6"] = frame["hs6"].str.zfill(6)
        frame["year"] = frame["month"].dt.year.astype("int16")
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(OUT / "comext_eu27_ch28_ch31_ch76_2002_2026.parquet", table.schema, compression="zstd")
        writer.write_table(table)
        focal_frames.append(frame[frame["hs6"].isin(FOCAL_HS6)].copy())
        summaries.append({
            "source_file": str(path), "rows": int(len(frame)),
            "year": int(frame["month"].dt.year.min()),
            "reporters": int(frame["reporter"].nunique()),
            "partners": int(frame["partner"].nunique()),
            "cn8_products": int(frame["cn8"].nunique()),
            "hs6_products": int(frame["hs6"].nunique()),
        })
        print(json.dumps(summaries[-1]), flush=True)
    if writer is not None:
        writer.close()
    focal = pd.concat(focal_frames, ignore_index=True).sort_values(["month", "hs6", "reporter", "partner", "cn8"])
    focal.to_parquet(OUT / "comext_focal_products_2002_2026.parquet", index=False, compression="zstd")
    focal.to_csv(OUT / "comext_focal_products_2002_2026.csv.gz", index=False, compression="gzip")
    with sqlite3.connect(OUT / "comext_focal_products_2002_2026.sqlite") as connection:
        focal.to_sql("bilateral_trade", connection, if_exists="replace", index=False, chunksize=100000)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_key ON bilateral_trade(reporter, partner, hs6, month)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_month ON bilateral_trade(month)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_trade_product ON bilateral_trade(hs6)")
    summary = {
        "source_files": len(source_files),
        "first_year": min(record["year"] for record in summaries),
        "latest_year": max(record["year"] for record in summaries),
        "full_rows": int(sum(record["rows"] for record in summaries)),
        "focal_rows": int(len(focal)),
        "focal_reporters": int(focal["reporter"].nunique()),
        "focal_partners": int(focal["partner"].nunique()),
        "focal_hs6_products": int(focal["hs6"].nunique()),
        "first_month": str(focal["month"].min().date()),
        "latest_month": str(focal["month"].max().date()),
        "annual_summaries": summaries,
    }
    (OUT / "comext_long_panel_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pd.DataFrame(summaries).to_csv(OUT / "comext_annual_coverage.csv", index=False)
    print(json.dumps({key: value for key, value in summary.items() if key != "annual_summaries"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
