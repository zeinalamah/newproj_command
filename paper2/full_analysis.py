#!/usr/bin/env python3
"""Construct and analyse the publication dataset for the Hormuz trade paper.

The program consumes only public-source artifacts:
  * Eurostat Comext monthly full files, 2002--2026;
  * IMF PortWatch daily chokepoint data;
  * World Bank Pink Sheet monthly commodity prices;
  * CEPII GeoDist bilateral distances;
  * Eurostat short-term producer-price and industrial-production series;
  * an optional UN Comtrade free-API validation extract.

It builds a long focal-product panel, network outcomes, replacement decompositions,
relationship entry/exit measures, synthetic-control shipping evidence, fixed-effect
models, historical placebos, and downstream exploratory tests.  Every intermediate
result used in the report is written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import warnings
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pycountry
import statsmodels.api as sm
from scipy import optimize, stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from econometrics import fe_ols, percentile_rank, randomization_pvalue, wald_test

warnings.filterwarnings("ignore", category=RuntimeWarning)

EU27 = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE",
}
HIGH_ROUTE = {"BH", "IQ", "IR", "KW", "QA"}
HIGH_ROUTE_NARROW = {"BH", "KW", "QA"}
PARTIAL_BYPASS = {"AE", "SA"}
NEARBY_NON_HORMUZ = {"OM"}
ALL_ROUTE = HIGH_ROUTE | PARTIAL_BYPASS
FOCAL = {
    "281410": "anhydrous_ammonia",
    "760110": "unwrought_aluminium_not_alloyed",
    "760120": "unwrought_aluminium_alloys",
}
PLACEBO_PRODUCT = "760120"
SHOCK = pd.Timestamp("2026-03-01")
PREWAR_START = pd.Timestamp("2023-01-01")
PREWAR_END = pd.Timestamp("2025-12-01")
MAIN_START = pd.Timestamp("2018-01-01")
MIN_RELATIONSHIP_VALUE = 100_000.0
MIN_RELATIONSHIP_MONTHS = 3


def mkdirs(root: Path) -> dict[str, Path]:
    paths = {
        "root": root,
        "data": root / "data",
        "tables": root / "tables",
        "figures": root / "figures",
        "audit": root / "audit",
        "scripts": root / "scripts",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso2_to_iso3(code: str) -> str | None:
    exceptions = {"XK": "XKX"}
    if code in exceptions:
        return exceptions[code]
    try:
        return pycountry.countries.get(alpha_2=code).alpha_3
    except Exception:
        return None


def month_difference(series: pd.Series, reference: pd.Timestamp) -> pd.Series:
    return (series.dt.year - reference.year) * 12 + series.dt.month - reference.month


def hhi(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").fillna(0).clip(lower=0)
    total = values.sum()
    return float(((values / total) ** 2).sum()) if total > 0 else np.nan


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    valid = values.notna() & weights.notna() & weights.gt(0)
    if not valid.any():
        return np.nan
    return float(np.average(values[valid], weights=weights[valid]))


def find_csv(root: Path, contains: Iterable[str]) -> Path | None:
    terms = [term.lower() for term in contains]
    candidates = []
    for path in root.rglob("*"):
        if path.is_file() and (path.suffix in {".csv", ".gz", ".parquet"} or path.name.endswith(".csv.gz")):
            lower = path.name.lower()
            if all(term in lower for term in terms):
                candidates.append(path)
    return sorted(candidates, key=lambda p: (len(str(p)), str(p)))[0] if candidates else None


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def load_distance(supplementary_root: Path) -> tuple[pd.DataFrame, dict]:
    path = find_csv(supplementary_root, ["geodist", "eu", "pairs"])
    if path is None:
        path = find_csv(supplementary_root, ["geodist", "full"])
    if path is None:
        return pd.DataFrame(columns=["partner", "reporter", "distance_km"]), {"status": "missing"}
    frame = read_table(path)
    lower = {str(column).lower(): column for column in frame.columns}
    origin = lower.get("iso_o") or lower.get("origin")
    destination = lower.get("iso_d") or lower.get("destination")
    distance = lower.get("distw") or lower.get("dist") or lower.get("distance")
    if not all([origin, destination, distance]):
        return pd.DataFrame(columns=["partner", "reporter", "distance_km"]), {
            "status": "unparsed", "columns": frame.columns.tolist(), "path": str(path)
        }
    iso3_to_iso2 = {}
    for country in pycountry.countries:
        iso3_to_iso2[country.alpha_3] = country.alpha_2
    iso3_to_iso2["XKX"] = "XK"
    out = frame[[origin, destination, distance]].copy()
    out["partner"] = out[origin].map(iso3_to_iso2)
    out["reporter"] = out[destination].map(iso3_to_iso2)
    out["distance_km"] = pd.to_numeric(out[distance], errors="coerce")
    out = out.dropna(subset=["partner", "reporter", "distance_km"])[["partner", "reporter", "distance_km"]]
    out = out.drop_duplicates(["partner", "reporter"])
    return out, {"status": "ok", "path": str(path), "rows": len(out)}


def clean_trade(frame: pd.DataFrame) -> pd.DataFrame:
    required = ["reporter", "partner", "month", "cn8", "hs6", "trade_value_eur", "quantity_kg"]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"Missing trade columns {missing}")
    out = frame[required].copy()
    out["month"] = pd.to_datetime(out["month"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    out["reporter"] = out["reporter"].astype(str).str.upper().str.strip()
    out["partner"] = out["partner"].astype(str).str.upper().str.strip()
    out["cn8"] = out["cn8"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(8)
    out["hs6"] = out["hs6"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    out["trade_value_eur"] = pd.to_numeric(out["trade_value_eur"], errors="coerce")
    out["quantity_kg"] = pd.to_numeric(out["quantity_kg"], errors="coerce")
    out.loc[out["trade_value_eur"].lt(0), "trade_value_eur"] = np.nan
    out.loc[out["quantity_kg"].lt(0), "quantity_kg"] = np.nan
    out = out[out["reporter"].isin(EU27) & out["partner"].str.fullmatch(r"[A-Z]{2}") & out["month"].notna()]
    return out


def build_trade_panels(comext_root: Path, distance: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    files = sorted(comext_root.rglob("comext_chapters_28_31_76_*.csv.gz"))
    if not files:
        files = sorted(comext_root.rglob("*.csv.gz"))
    focal_frames: list[pd.DataFrame] = []
    network_frames: list[pd.DataFrame] = []
    eu_origin_frames: list[pd.DataFrame] = []
    coverage_rows = []
    distance_keys = distance if not distance.empty else None

    for path in files:
        try:
            frame = clean_trade(pd.read_csv(path, low_memory=False))
        except Exception as exc:
            coverage_rows.append({"file": str(path), "status": "failed", "error": repr(exc)})
            continue
        if frame.empty:
            coverage_rows.append({"file": str(path), "status": "empty"})
            continue
        frame["product_label"] = frame["hs6"].map(FOCAL)
        frame["intra_eu"] = frame["partner"].isin(EU27).astype("int8")
        frame["high_route"] = frame["partner"].isin(HIGH_ROUTE).astype("int8")
        frame["high_route_narrow"] = frame["partner"].isin(HIGH_ROUTE_NARROW).astype("int8")
        frame["partial_bypass"] = frame["partner"].isin(PARTIAL_BYPASS).astype("int8")
        frame["all_route"] = frame["partner"].isin(ALL_ROUTE).astype("int8")
        frame["oman"] = frame["partner"].isin(NEARBY_NON_HORMUZ).astype("int8")
        if distance_keys is not None:
            frame = frame.merge(distance_keys, on=["partner", "reporter"], how="left", validate="many_to_one")
        else:
            frame["distance_km"] = np.nan
        frame["unit_value_eur_per_kg"] = np.where(
            frame["quantity_kg"].gt(0), frame["trade_value_eur"] / frame["quantity_kg"], np.nan
        )
        focal = frame[frame["hs6"].isin(FOCAL)].copy()
        if not focal.empty:
            focal_frames.append(focal)

        x = frame.copy()
        x["positive_supplier"] = x["quantity_kg"].fillna(0).gt(0).astype("int8")
        x["route_q"] = x["quantity_kg"].fillna(0) * x["high_route"]
        x["route_v"] = x["trade_value_eur"].fillna(0) * x["high_route"]
        x["partial_q"] = x["quantity_kg"].fillna(0) * x["partial_bypass"]
        x["intra_q"] = x["quantity_kg"].fillna(0) * x["intra_eu"]
        keys = ["reporter", "hs6", "month"]
        totals = x.groupby(keys, as_index=False).agg(
            total_quantity_kg=("quantity_kg", "sum"),
            total_value_eur=("trade_value_eur", "sum"),
            high_route_quantity_kg=("route_q", "sum"),
            high_route_value_eur=("route_v", "sum"),
            partial_bypass_quantity_kg=("partial_q", "sum"),
            intra_eu_quantity_kg=("intra_q", "sum"),
            supplier_count=("positive_supplier", "sum"),
        )
        conc = x.groupby(keys, as_index=False).agg(
            supplier_hhi=("quantity_kg", hhi),
            weighted_distance_km=("distance_km", lambda s, g=x: np.nan),
        )
        # Weighted distance is calculated separately because groupby.agg does not
        # expose the quantity weights to a named aggregation.
        dist_rows = []
        for key, group in x.groupby(keys, sort=False):
            dist_rows.append((*key, weighted_mean(group["distance_km"], group["quantity_kg"].fillna(0))))
        distances = pd.DataFrame(dist_rows, columns=[*keys, "weighted_distance_km"])
        conc = conc.drop(columns=["weighted_distance_km"]).merge(distances, on=keys, how="left")
        monthly = totals.merge(conc, on=keys, how="left", validate="one_to_one")
        monthly["effective_suppliers"] = np.where(monthly["supplier_hhi"].gt(0), 1 / monthly["supplier_hhi"], np.nan)
        monthly["unit_value_eur_per_kg"] = np.where(
            monthly["total_quantity_kg"].gt(0), monthly["total_value_eur"] / monthly["total_quantity_kg"], np.nan
        )
        monthly["high_route_share"] = np.where(
            monthly["total_quantity_kg"].gt(0), monthly["high_route_quantity_kg"] / monthly["total_quantity_kg"], np.nan
        )
        monthly["intra_eu_share"] = np.where(
            monthly["total_quantity_kg"].gt(0), monthly["intra_eu_quantity_kg"] / monthly["total_quantity_kg"], np.nan
        )
        network_frames.append(monthly)

        origin = frame.groupby(["partner", "hs6", "month"], as_index=False).agg(
            eu_quantity_kg=("quantity_kg", "sum"), eu_value_eur=("trade_value_eur", "sum")
        )
        eu_origin_frames.append(origin)
        coverage_rows.append({
            "file": str(path), "status": "ok", "rows": len(frame),
            "first_month": str(frame["month"].min().date()), "latest_month": str(frame["month"].max().date()),
            "reporters": frame["reporter"].nunique(), "partners": frame["partner"].nunique(),
            "cn8": frame["cn8"].nunique(), "hs6": frame["hs6"].nunique(),
        })

    if not focal_frames:
        raise RuntimeError("No focal Comext observations were found")
    focal = pd.concat(focal_frames, ignore_index=True)
    focal = focal.groupby(
        ["reporter", "partner", "month", "cn8", "hs6", "product_label", "intra_eu", "high_route",
         "high_route_narrow", "partial_bypass", "all_route", "oman", "distance_km"],
        as_index=False, dropna=False,
    ).agg(trade_value_eur=("trade_value_eur", "sum"), quantity_kg=("quantity_kg", "sum"))
    focal["unit_value_eur_per_kg"] = np.where(focal["quantity_kg"].gt(0), focal["trade_value_eur"] / focal["quantity_kg"], np.nan)
    focal = focal.sort_values(["hs6", "reporter", "partner", "month", "cn8"]).reset_index(drop=True)
    network = pd.concat(network_frames, ignore_index=True)
    network = network.sort_values(["hs6", "reporter", "month"]).reset_index(drop=True)
    origin_month = pd.concat(eu_origin_frames, ignore_index=True).groupby(["partner", "hs6", "month"], as_index=False).sum(numeric_only=True)
    coverage = pd.DataFrame(coverage_rows)

    focal.to_parquet(paths["data"] / "focal_bilateral_cn8_month.parquet", index=False, compression="zstd")
    network.to_parquet(paths["data"] / "all_products_importer_network_month.parquet", index=False, compression="zstd")
    origin_month.to_parquet(paths["data"] / "all_products_origin_eu_month.parquet", index=False, compression="zstd")
    coverage.to_csv(paths["audit"] / "comext_file_coverage.csv", index=False)
    return focal, network, origin_month, coverage, {
        "files_found": len(files), "files_ok": int((coverage.get("status") == "ok").sum()) if len(coverage) else 0,
        "focal_rows": len(focal), "network_rows": len(network), "origin_month_rows": len(origin_month),
        "first_month": str(focal["month"].min().date()), "latest_month": str(focal["month"].max().date()),
        "reporters": focal["reporter"].nunique(), "partners": focal["partner"].nunique(),
    }


def add_entry_exit_measures(focal: pd.DataFrame, network: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    pre = focal[focal["month"].between(PREWAR_START, PREWAR_END)].copy()
    established = pre.groupby(["reporter", "hs6", "partner"], as_index=False).agg(
        prewar_months=("quantity_kg", lambda x: int(x.fillna(0).gt(0).sum())),
        prewar_value_eur=("trade_value_eur", "sum"), prewar_quantity_kg=("quantity_kg", "sum")
    )
    established["established"] = (
        established["prewar_months"].ge(MIN_RELATIONSHIP_MONTHS)
        & established["prewar_value_eur"].ge(MIN_RELATIONSHIP_VALUE)
    )
    enriched = focal.merge(established, on=["reporter", "hs6", "partner"], how="left")
    enriched[["prewar_months", "prewar_value_eur", "prewar_quantity_kg"]] = enriched[["prewar_months", "prewar_value_eur", "prewar_quantity_kg"]].fillna(0)
    enriched["established"] = enriched["established"].fillna(False)
    enriched["entrant_quantity"] = np.where(~enriched["established"], enriched["quantity_kg"].fillna(0), 0)
    entrant = enriched.groupby(["reporter", "hs6", "month"], as_index=False).agg(
        entrant_quantity_kg=("entrant_quantity", "sum")
    )
    pre_counts = established[established["established"]].groupby(["reporter", "hs6"], as_index=False).agg(
        established_supplier_count=("partner", "nunique")
    )
    active = enriched[enriched["established"] & enriched["quantity_kg"].fillna(0).gt(0)].groupby(
        ["reporter", "hs6", "month"], as_index=False
    ).agg(active_established_suppliers=("partner", "nunique"))
    measures = network[network["hs6"].isin(FOCAL)].merge(entrant, on=["reporter", "hs6", "month"], how="left")
    measures = measures.merge(pre_counts, on=["reporter", "hs6"], how="left").merge(active, on=["reporter", "hs6", "month"], how="left")
    for column in ["entrant_quantity_kg", "established_supplier_count", "active_established_suppliers"]:
        measures[column] = measures[column].fillna(0)
    measures["entrant_quantity_share"] = np.where(
        measures["total_quantity_kg"].gt(0), measures["entrant_quantity_kg"] / measures["total_quantity_kg"], 0
    )
    measures["lost_established_supplier_share"] = np.where(
        measures["established_supplier_count"].gt(0),
        1 - measures["active_established_suppliers"] / measures["established_supplier_count"], np.nan,
    )
    return measures, established


def prewar_exposure(focal: pd.DataFrame) -> pd.DataFrame:
    pre = focal[focal["month"].between(PREWAR_START, PREWAR_END)].copy()
    pre["route_q"] = pre["quantity_kg"].fillna(0) * pre["high_route"]
    pre["narrow_q"] = pre["quantity_kg"].fillna(0) * pre["high_route_narrow"]
    pre["partial_q"] = pre["quantity_kg"].fillna(0) * pre["partial_bypass"]
    out = pre.groupby(["reporter", "hs6", "product_label"], as_index=False).agg(
        prewar_total_quantity_kg=("quantity_kg", "sum"), prewar_total_value_eur=("trade_value_eur", "sum"),
        prewar_route_quantity_kg=("route_q", "sum"), prewar_narrow_route_quantity_kg=("narrow_q", "sum"),
        prewar_partial_quantity_kg=("partial_q", "sum"), prewar_supplier_count=("partner", "nunique"),
    )
    for name, numerator in [
        ("route_exposure", "prewar_route_quantity_kg"),
        ("narrow_route_exposure", "prewar_narrow_route_quantity_kg"),
        ("partial_bypass_exposure", "prewar_partial_quantity_kg"),
    ]:
        out[name] = np.where(out["prewar_total_quantity_kg"].gt(0), out[numerator] / out["prewar_total_quantity_kg"], 0)
    return out


def build_balanced_relationship_panel(focal: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    sample = focal[(focal["month"].between(start, end)) & (~focal["intra_eu"].astype(bool))].copy()
    pre = focal[focal["month"].between(PREWAR_START, PREWAR_END)].groupby(
        ["reporter", "partner", "hs6", "product_label"], as_index=False
    ).agg(pre_value=("trade_value_eur", "sum"), pre_months=("quantity_kg", lambda x: int(x.fillna(0).gt(0).sum())))
    post_rel = focal[focal["month"].ge(SHOCK)].groupby(
        ["reporter", "partner", "hs6", "product_label"], as_index=False
    ).agg(post_value=("trade_value_eur", "sum"))
    relationships = pre.merge(post_rel, on=["reporter", "partner", "hs6", "product_label"], how="outer").fillna(0)
    relationships = relationships[(relationships["pre_months"].ge(1)) | (relationships["post_value"].gt(0))]
    relationships = relationships[["reporter", "partner", "hs6", "product_label"]]
    months = pd.DataFrame({"month": pd.date_range(start, end, freq="MS")})
    grid = relationships.merge(months, how="cross")
    observed = sample.groupby(["reporter", "partner", "hs6", "product_label", "month"], as_index=False).agg(
        quantity_kg=("quantity_kg", "sum"), trade_value_eur=("trade_value_eur", "sum"),
        distance_km=("distance_km", "first")
    )
    panel = grid.merge(observed, on=["reporter", "partner", "hs6", "product_label", "month"], how="left")
    panel[["quantity_kg", "trade_value_eur"]] = panel[["quantity_kg", "trade_value_eur"]].fillna(0)
    panel["distance_km"] = panel.groupby(["reporter", "partner"])["distance_km"].transform(lambda x: x.ffill().bfill())
    panel["high_route"] = panel["partner"].isin(HIGH_ROUTE).astype("int8")
    panel["high_route_narrow"] = panel["partner"].isin(HIGH_ROUTE_NARROW).astype("int8")
    panel["partial_bypass"] = panel["partner"].isin(PARTIAL_BYPASS).astype("int8")
    panel["oman"] = panel["partner"].isin(NEARBY_NON_HORMUZ).astype("int8")
    panel["post"] = panel["month"].ge(SHOCK).astype("int8")
    panel["relative_month"] = month_difference(panel["month"], SHOCK)
    panel["ihs_quantity"] = np.arcsinh(panel["quantity_kg"] / 1000)
    panel["active"] = panel["quantity_kg"].gt(0).astype("int8")
    panel["relationship_fe"] = panel["reporter"] + "|" + panel["partner"] + "|" + panel["hs6"]
    panel["importer_product_month_fe"] = panel["reporter"] + "|" + panel["hs6"] + "|" + panel["month"].astype(str)
    return panel


def route_trade_models(panel: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    results = []
    event_rows = []
    definitions = {
        "high_route": "high_route",
        "narrow_route": "high_route_narrow",
        "all_route_including_bypass": None,
    }
    panel = panel.copy()
    panel["all_route"] = ((panel["high_route"] == 1) | (panel["partial_bypass"] == 1)).astype("int8")
    definitions["all_route_including_bypass"] = "all_route"
    for definition, treatment in definitions.items():
        for product in ["all", *FOCAL.keys()]:
            sample = panel.copy() if product == "all" else panel[panel["hs6"].eq(product)].copy()
            if definition != "all_route_including_bypass":
                sample = sample[~sample["partial_bypass"].astype(bool)]
            sample["treated_post"] = sample[treatment] * sample["post"]
            for outcome in ["ihs_quantity", "active"]:
                try:
                    fit = fe_ols(
                        sample, outcome, ["treated_post"],
                        ["relationship_fe", "importer_product_month_fe"], "partner"
                    )
                    row = fit.frame().iloc[0].to_dict()
                    row.update({"model": "static_route", "definition": definition, "product": product, "outcome": outcome})
                    results.append(row)
                except Exception as exc:
                    results.append({"model": "static_route", "definition": definition, "product": product, "outcome": outcome, "error": repr(exc)})

    # Dynamic event studies use a compact two-year pre-period.  Months outside
    # [-18,+5] are excluded; February 2026 (-1) is the reference month.
    dynamic = panel[panel["relative_month"].between(-18, 5)].copy()
    dynamic = dynamic[~dynamic["partial_bypass"].astype(bool)]
    event_months = [month for month in range(-18, 6) if month != -1]
    for month in event_months:
        dynamic[f"event_{month}"] = (dynamic["relative_month"].eq(month) * dynamic["high_route"]).astype(float)
    terms = [f"event_{month}" for month in event_months]
    for product in [*FOCAL.keys()]:
        sample = dynamic[dynamic["hs6"].eq(product)].copy()
        for outcome in ["ihs_quantity", "active"]:
            try:
                fit = fe_ols(sample, outcome, terms, ["relationship_fe", "importer_product_month_fe"], "partner")
                table = fit.frame()
                table["relative_month"] = table["term"].str.replace("event_", "", regex=False).astype(int)
                table["product"] = product
                table["outcome"] = outcome
                event_rows.append(table)
                preterms = [term for term in terms if int(term.replace("event_", "")) <= -2]
                pretest = wald_test(fit, preterms)
                results.append({
                    "model": "dynamic_pretrend_test", "definition": "high_route", "product": product,
                    "outcome": outcome, "coefficient": np.nan, "std_error": np.nan, "p_value": pretest["p_value"],
                    "n_obs": fit.nobs, "clusters": fit.clusters, "wald_statistic": pretest["statistic"], "wald_df": pretest["df"],
                })
            except Exception as exc:
                results.append({"model": "dynamic_route", "definition": "high_route", "product": product, "outcome": outcome, "error": repr(exc)})
    result_frame = pd.DataFrame(results)
    events = pd.concat(event_rows, ignore_index=True) if event_rows else pd.DataFrame()
    result_frame.to_csv(paths["tables"] / "route_trade_models.csv", index=False)
    events.to_csv(paths["tables"] / "route_trade_event_study.csv", index=False)

    if not events.empty:
        for (product, outcome), group in events.groupby(["product", "outcome"]):
            group = group.sort_values("relative_month")
            fig, ax = plt.subplots(figsize=(9, 5.5))
            ax.axhline(0, linewidth=0.8)
            ax.axvline(-0.5, linestyle="--", linewidth=0.8)
            ax.errorbar(group["relative_month"], group["coefficient"], yerr=1.96 * group["std_error"], fmt="o-")
            ax.set_title(f"Route-exposed origin event study: {FOCAL.get(product, product)}")
            ax.set_xlabel("Months relative to March 2026")
            ax.set_ylabel("Coefficient" if outcome == "active" else "Effect on asinh(quantity tonnes)")
            fig.tight_layout()
            fig.savefig(paths["figures"] / f"route_event_{product}_{outcome}.png", dpi=180)
            plt.close(fig)
    return result_frame, events, {"balanced_rows": len(panel), "relationships": panel["relationship_fe"].nunique()}


def network_models(measures: pd.DataFrame, exposure: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    sample = measures.merge(exposure, on=["reporter", "hs6"], how="left", suffixes=("", "_exposure"))
    sample = sample[sample["month"].between(pd.Timestamp("2024-01-01"), sample["month"].max())].copy()
    sample["product_label"] = sample["hs6"].map(FOCAL)
    sample["post"] = sample["month"].ge(SHOCK).astype("int8")
    sample["relative_month"] = month_difference(sample["month"], SHOCK)
    sample["exposure_post"] = sample["route_exposure"].fillna(0) * sample["post"]
    sample["narrow_exposure_post"] = sample["narrow_route_exposure"].fillna(0) * sample["post"]
    sample["log_quantity"] = np.log1p(sample["total_quantity_kg"] / 1000)
    sample["log_unit_value"] = np.log(sample["unit_value_eur_per_kg"].where(sample["unit_value_eur_per_kg"].gt(0)))
    sample["log_distance"] = np.log(sample["weighted_distance_km"].where(sample["weighted_distance_km"].gt(0)))
    sample["importer_product_fe"] = sample["reporter"] + "|" + sample["hs6"]
    sample["importer_month_fe"] = sample["reporter"] + "|" + sample["month"].astype(str)
    sample["product_month_fe"] = sample["hs6"] + "|" + sample["month"].astype(str)
    outcomes = [
        "log_quantity", "log_unit_value", "supplier_hhi", "effective_suppliers", "supplier_count",
        "log_distance", "entrant_quantity_share", "lost_established_supplier_share",
    ]
    rows = []
    for treatment in ["exposure_post", "narrow_exposure_post"]:
        for outcome in outcomes:
            try:
                fit = fe_ols(
                    sample, outcome, [treatment],
                    ["importer_product_fe", "importer_month_fe", "product_month_fe"], "reporter"
                )
                row = fit.frame().iloc[0].to_dict()
                row.update({"model": "network_static", "outcome": outcome, "treatment": treatment, "sample": "all_focal"})
                rows.append(row)
            except Exception as exc:
                rows.append({"model": "network_static", "outcome": outcome, "treatment": treatment, "sample": "all_focal", "error": repr(exc)})
        for product in FOCAL:
            product_sample = sample[sample["hs6"].eq(product)].copy()
            for outcome in outcomes:
                try:
                    # With one product, product-month FE is redundant with month;
                    # importer-month absorbs all variation, so use importer-product
                    # and month FE for the country exposure gradient.
                    product_sample["month_fe"] = product_sample["month"].astype(str)
                    fit = fe_ols(product_sample, outcome, [treatment], ["importer_product_fe", "month_fe"], "reporter")
                    row = fit.frame().iloc[0].to_dict()
                    row.update({"model": "network_static", "outcome": outcome, "treatment": treatment, "sample": product})
                    rows.append(row)
                except Exception as exc:
                    rows.append({"model": "network_static", "outcome": outcome, "treatment": treatment, "sample": product, "error": repr(exc)})

    event_months = [month for month in range(-12, 6) if month != -1]
    event_sample = sample[sample["relative_month"].between(-12, 5)].copy()
    event_terms = []
    for month in event_months:
        term = f"exposure_event_{month}"
        event_sample[term] = event_sample["route_exposure"].fillna(0) * event_sample["relative_month"].eq(month)
        event_terms.append(term)
    event_rows = []
    for outcome in ["log_quantity", "log_unit_value", "supplier_hhi", "log_distance", "entrant_quantity_share"]:
        try:
            fit = fe_ols(
                event_sample, outcome, event_terms,
                ["importer_product_fe", "importer_month_fe", "product_month_fe"], "reporter"
            )
            table = fit.frame()
            table["relative_month"] = table["term"].str.replace("exposure_event_", "", regex=False).astype(int)
            table["outcome"] = outcome
            event_rows.append(table)
            pre = [term for term in event_terms if int(term.replace("exposure_event_", "")) <= -2]
            test = wald_test(fit, pre)
            rows.append({"model": "network_event_pretrend", "outcome": outcome, "treatment": "route_exposure", "sample": "all_focal", "p_value": test["p_value"], "wald_statistic": test["statistic"], "wald_df": test["df"], "n_obs": fit.nobs, "clusters": fit.clusters})
        except Exception as exc:
            rows.append({"model": "network_event", "outcome": outcome, "error": repr(exc)})
    results = pd.DataFrame(rows)
    events = pd.concat(event_rows, ignore_index=True) if event_rows else pd.DataFrame()
    results.to_csv(paths["tables"] / "network_reorganization_models.csv", index=False)
    events.to_csv(paths["tables"] / "network_reorganization_event_study.csv", index=False)
    if not events.empty:
        for outcome, group in events.groupby("outcome"):
            group = group.sort_values("relative_month")
            fig, ax = plt.subplots(figsize=(9, 5.5))
            ax.axhline(0, linewidth=0.8)
            ax.axvline(-0.5, linestyle="--", linewidth=0.8)
            ax.errorbar(group["relative_month"], group["coefficient"], yerr=1.96 * group["std_error"], fmt="o-")
            ax.set_title(f"Exposure-gradient event study: {outcome}")
            ax.set_xlabel("Months relative to March 2026")
            ax.set_ylabel("Coefficient on prewar Hormuz exposure")
            fig.tight_layout()
            fig.savefig(paths["figures"] / f"network_event_{outcome}.png", dpi=180)
            plt.close(fig)
    sample.to_parquet(paths["data"] / "focal_importer_product_network_month.parquet", index=False, compression="zstd")
    return results, events


def replacement_decomposition(focal: pd.DataFrame, established: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = focal.copy()
    established_key = established[["reporter", "hs6", "partner", "established"]]
    data = data.merge(established_key, on=["reporter", "hs6", "partner"], how="left")
    data["established"] = data["established"].fillna(False)
    data["calendar_month"] = data["month"].dt.month
    baseline = data[data["month"].between("2023-01-01", "2025-12-01")].groupby(
        ["reporter", "hs6", "partner", "calendar_month"], as_index=False
    ).agg(counterfactual_quantity_kg=("quantity_kg", "mean"), counterfactual_value_eur=("trade_value_eur", "mean"))
    post = data[data["month"].ge(SHOCK)].merge(
        baseline, on=["reporter", "hs6", "partner", "calendar_month"], how="left"
    )
    post[["counterfactual_quantity_kg", "counterfactual_value_eur"]] = post[["counterfactual_quantity_kg", "counterfactual_value_eur"]].fillna(0)
    post["actual_quantity_kg"] = post["quantity_kg"].fillna(0)
    post["change_quantity_kg"] = post["actual_quantity_kg"] - post["counterfactual_quantity_kg"]
    post["lost_route_quantity_kg"] = np.where(post["high_route"].eq(1), np.maximum(-post["change_quantity_kg"], 0), 0)
    post["incumbent_gain_kg"] = np.where((post["high_route"].eq(0)) & post["established"], np.maximum(post["change_quantity_kg"], 0), 0)
    post["entrant_gain_kg"] = np.where((post["high_route"].eq(0)) & (~post["established"]), np.maximum(post["change_quantity_kg"], 0), 0)
    post["supplier_unit_value"] = np.where(post["actual_quantity_kg"].gt(0), post["trade_value_eur"] / post["actual_quantity_kg"], np.nan)
    post["counterfactual_unit_value"] = np.where(post["counterfactual_quantity_kg"].gt(0), post["counterfactual_value_eur"] / post["counterfactual_quantity_kg"], np.nan)
    keys = ["reporter", "hs6", "product_label", "month"]
    rows = []
    for key, group in post.groupby(keys):
        lost = group["lost_route_quantity_kg"].sum()
        incumbent = group["incumbent_gain_kg"].sum()
        entrant = group["entrant_gain_kg"].sum()
        replacement = incumbent + entrant
        route_base = group.loc[group["high_route"].eq(1), "counterfactual_quantity_kg"].sum()
        route_actual = group.loc[group["high_route"].eq(1), "actual_quantity_kg"].sum()
        nonroute_actual = group.loc[group["high_route"].eq(0), "actual_quantity_kg"].sum()
        pre_route_price = weighted_mean(
            group.loc[group["high_route"].eq(1), "counterfactual_unit_value"],
            group.loc[group["high_route"].eq(1), "counterfactual_quantity_kg"],
        )
        replacement_price = weighted_mean(
            group.loc[(group["high_route"].eq(0)) & group["change_quantity_kg"].gt(0), "supplier_unit_value"],
            group.loc[(group["high_route"].eq(0)) & group["change_quantity_kg"].gt(0), "change_quantity_kg"],
        )
        replacement_distance = weighted_mean(
            group.loc[(group["high_route"].eq(0)) & group["change_quantity_kg"].gt(0), "distance_km"],
            group.loc[(group["high_route"].eq(0)) & group["change_quantity_kg"].gt(0), "change_quantity_kg"],
        )
        displaced_distance = weighted_mean(
            group.loc[group["high_route"].eq(1), "distance_km"],
            group.loc[group["high_route"].eq(1), "lost_route_quantity_kg"],
        )
        rows.append({
            "reporter": key[0], "hs6": key[1], "product_label": key[2], "month": key[3],
            "counterfactual_route_quantity_kg": route_base, "actual_route_quantity_kg": route_actual,
            "lost_route_quantity_kg": lost, "incumbent_replacement_kg": incumbent,
            "entrant_replacement_kg": entrant, "total_replacement_kg": replacement,
            "unreplaced_shortfall_kg": max(lost - replacement, 0),
            "replacement_ratio": replacement / lost if lost > 0 else np.nan,
            "entrant_share_of_replacement": entrant / replacement if replacement > 0 else np.nan,
            "prewar_route_unit_value": pre_route_price, "replacement_unit_value": replacement_price,
            "replacement_premium_log": math.log(replacement_price / pre_route_price) if replacement_price and pre_route_price and replacement_price > 0 and pre_route_price > 0 else np.nan,
            "replacement_distance_km": replacement_distance, "displaced_distance_km": displaced_distance,
            "distance_change_km": replacement_distance - displaced_distance if pd.notna(replacement_distance) and pd.notna(displaced_distance) else np.nan,
            "nonroute_actual_quantity_kg": nonroute_actual,
        })
    decomposition = pd.DataFrame(rows)
    aggregate = decomposition.groupby(["hs6", "product_label"], as_index=False).agg(
        lost_route_quantity_kg=("lost_route_quantity_kg", "sum"),
        incumbent_replacement_kg=("incumbent_replacement_kg", "sum"),
        entrant_replacement_kg=("entrant_replacement_kg", "sum"),
        unreplaced_shortfall_kg=("unreplaced_shortfall_kg", "sum"),
        median_replacement_ratio=("replacement_ratio", "median"),
        weighted_replacement_premium_log=("replacement_premium_log", "median"),
        median_distance_change_km=("distance_change_km", "median"),
        countries_with_route_loss=("lost_route_quantity_kg", lambda x: int(x.gt(0).groupby(decomposition.loc[x.index, "reporter"]).any().sum())),
    )
    aggregate["aggregate_replacement_ratio"] = (
        aggregate["incumbent_replacement_kg"] + aggregate["entrant_replacement_kg"]
    ) / aggregate["lost_route_quantity_kg"].replace(0, np.nan)
    decomposition.to_csv(paths["tables"] / "replacement_decomposition_country_month.csv", index=False)
    aggregate.to_csv(paths["tables"] / "replacement_decomposition_product.csv", index=False)
    if not aggregate.empty:
        fig, ax = plt.subplots(figsize=(9, 5.5))
        x = np.arange(len(aggregate))
        ax.bar(x, aggregate["incumbent_replacement_kg"] / 1e6, label="Incumbent suppliers")
        ax.bar(x, aggregate["entrant_replacement_kg"] / 1e6, bottom=aggregate["incumbent_replacement_kg"] / 1e6, label="New suppliers")
        ax.plot(x, aggregate["lost_route_quantity_kg"] / 1e6, marker="o", linestyle="--", label="Lost route-exposed supply")
        ax.set_xticks(x)
        ax.set_xticklabels(aggregate["product_label"], rotation=20, ha="right")
        ax.set_ylabel("Million kilograms")
        ax.set_title("Replacement of disrupted route-exposed imports")
        ax.legend()
        fig.tight_layout()
        fig.savefig(paths["figures"] / "replacement_decomposition.png", dpi=180)
        plt.close(fig)
    return decomposition, aggregate


def relationship_exit_model(focal: pd.DataFrame, established: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    active_pre = established[established["established"]].copy()
    post = focal[focal["month"].ge(SHOCK)].groupby(["reporter", "hs6", "partner"], as_index=False).agg(
        post_months=("quantity_kg", lambda x: int(x.fillna(0).gt(0).sum())), post_value=("trade_value_eur", "sum"),
        distance_km=("distance_km", "first")
    )
    frame = active_pre.merge(post, on=["reporter", "hs6", "partner"], how="left")
    frame[["post_months", "post_value"]] = frame[["post_months", "post_value"]].fillna(0)
    frame["exit"] = frame["post_months"].eq(0).astype(float)
    frame["high_route"] = frame["partner"].isin(HIGH_ROUTE).astype(float)
    frame["partial_bypass"] = frame["partner"].isin(PARTIAL_BYPASS).astype(float)
    frame["log_prewar_value"] = np.log1p(frame["prewar_value_eur"])
    frame["log_distance"] = np.log(frame["distance_km"].where(frame["distance_km"].gt(0)))
    frame["importer_product_fe"] = frame["reporter"] + "|" + frame["hs6"]
    rows = []
    for treatment in ["high_route", "partial_bypass"]:
        try:
            fit = fe_ols(frame, "exit", [treatment, "log_prewar_value", "log_distance"], ["importer_product_fe"], "partner")
            table = fit.frame()
            table["model"] = "relationship_exit_lpm"
            table["treatment_definition"] = treatment
            rows.append(table)
        except Exception as exc:
            rows.append(pd.DataFrame([{"model": "relationship_exit_lpm", "treatment_definition": treatment, "error": repr(exc)}]))
    result = pd.concat(rows, ignore_index=True)
    result.to_csv(paths["tables"] / "relationship_exit_models.csv", index=False)
    frame.to_csv(paths["data"] / "prewar_relationship_survival.csv", index=False)
    return result


def price_decomposition(focal: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    data = focal[(focal["month"].between("2025-01-01", focal["month"].max())) & focal["quantity_kg"].gt(0) & focal["unit_value_eur_per_kg"].gt(0)].copy()
    log_uv = np.log(data["unit_value_eur_per_kg"])
    lower = data.assign(log_uv=log_uv).groupby(["hs6", "cn8"])["log_uv"].transform(lambda x: x.quantile(0.01))
    upper = data.assign(log_uv=log_uv).groupby(["hs6", "cn8"])["log_uv"].transform(lambda x: x.quantile(0.99))
    data = data[(log_uv >= lower) & (log_uv <= upper)].copy()
    base = data[data["month"].between("2025-01-01", "2025-12-01")].groupby(
        ["reporter", "hs6", "partner", "cn8"], as_index=False
    ).agg(base_quantity=("quantity_kg", "sum"), base_value=("trade_value_eur", "sum"))
    base["base_price"] = np.where(base["base_quantity"].gt(0), base["base_value"] / base["base_quantity"], np.nan)
    base["base_share"] = base["base_quantity"] / base.groupby(["reporter", "hs6"])["base_quantity"].transform("sum")
    merged = data.merge(base, on=["reporter", "hs6", "partner", "cn8"], how="left")
    merged["price_relative"] = merged["unit_value_eur_per_kg"] / merged["base_price"]
    rows = []
    for key, group in merged.groupby(["reporter", "hs6", "product_label", "month"]):
        current_uv = group["trade_value_eur"].sum() / group["quantity_kg"].sum() if group["quantity_kg"].sum() > 0 else np.nan
        valid = group["base_share"].notna() & group["price_relative"].gt(0)
        if valid.any():
            weights = group.loc[valid, "base_share"]
            weights = weights / weights.sum()
            fixed_index = float(np.exp(np.sum(weights * np.log(group.loc[valid, "price_relative"]))))
            base_uv = weighted_mean(group.loc[valid, "base_price"], weights)
            current_index = current_uv / base_uv if base_uv and base_uv > 0 else np.nan
        else:
            fixed_index = np.nan
            current_index = np.nan
        rows.append({
            "reporter": key[0], "hs6": key[1], "product_label": key[2], "month": key[3],
            "current_unit_value": current_uv, "fixed_supplier_price_index": fixed_index,
            "current_basket_unit_value_index": current_index,
            "composition_effect_log": math.log(current_index / fixed_index) if current_index and fixed_index and current_index > 0 and fixed_index > 0 else np.nan,
            "continuing_supplier_weight": group.loc[valid, "base_share"].sum() if valid.any() else 0,
        })
    out = pd.DataFrame(rows)
    out.to_csv(paths["tables"] / "unit_value_price_composition_decomposition.csv", index=False)
    return out


def historical_placebos(focal: pd.DataFrame, network: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    years = range(max(2006, int(focal["month"].dt.year.min()) + 3), 2027)
    rows = []
    outcomes = ["log_quantity", "log_unit_value", "supplier_hhi", "supplier_count", "log_distance"]
    focal_network = network[network["hs6"].isin(FOCAL)].copy()
    focal_network["log_quantity"] = np.log1p(focal_network["total_quantity_kg"] / 1000)
    focal_network["log_unit_value"] = np.log(focal_network["unit_value_eur_per_kg"].where(focal_network["unit_value_eur_per_kg"].gt(0)))
    focal_network["log_distance"] = np.log(focal_network["weighted_distance_km"].where(focal_network["weighted_distance_km"].gt(0)))

    for year in years:
        pre_start = pd.Timestamp(year=year - 3, month=1, day=1)
        pre_end = pd.Timestamp(year=year - 1, month=12, day=1)
        pre = focal[focal["month"].between(pre_start, pre_end)].copy()
        if pre.empty:
            continue
        pre["route_q"] = pre["quantity_kg"].fillna(0) * pre["high_route"]
        exposure = pre.groupby(["reporter", "hs6"], as_index=False).agg(total=("quantity_kg", "sum"), route=("route_q", "sum"))
        exposure["exposure"] = np.where(exposure["total"].gt(0), exposure["route"] / exposure["total"], 0)
        start = pd.Timestamp(year=year, month=1, day=1)
        end_month = 6 if year < 2026 else int(focal_network.loc[focal_network["month"].dt.year.eq(year), "month"].dt.month.max())
        if not np.isfinite(end_month) or end_month < 3:
            continue
        end = pd.Timestamp(year=year, month=int(end_month), day=1)
        sample = focal_network[focal_network["month"].between(start, end)].merge(exposure[["reporter", "hs6", "exposure"]], on=["reporter", "hs6"], how="left")
        if sample.empty:
            continue
        sample["post"] = sample["month"].dt.month.ge(3).astype("int8")
        sample["exposure_post"] = sample["exposure"].fillna(0) * sample["post"]
        sample["ip_fe"] = sample["reporter"] + "|" + sample["hs6"]
        sample["importer_month_fe"] = sample["reporter"] + "|" + sample["month"].astype(str)
        sample["product_month_fe"] = sample["hs6"] + "|" + sample["month"].astype(str)
        for outcome in outcomes:
            try:
                fit = fe_ols(sample, outcome, ["exposure_post"], ["ip_fe", "importer_month_fe", "product_month_fe"], "reporter")
                result = fit.frame().iloc[0].to_dict()
                result.update({"pseudo_year": year, "outcome": outcome, "post_months": int(end_month - 2)})
                rows.append(result)
            except Exception as exc:
                rows.append({"pseudo_year": year, "outcome": outcome, "error": repr(exc)})
    placebo = pd.DataFrame(rows)
    summary_rows = []
    for outcome, group in placebo.dropna(subset=["coefficient"]).groupby("outcome"):
        actual = group[group["pseudo_year"].eq(2026)]
        pre = group[group["pseudo_year"].lt(2026)]
        if actual.empty:
            continue
        observed = float(actual.iloc[0]["coefficient"])
        summary_rows.append({
            "outcome": outcome, "coefficient_2026": observed,
            "historical_placebo_mean": pre["coefficient"].mean(),
            "historical_placebo_sd": pre["coefficient"].std(),
            "percentile_rank_2026": percentile_rank(observed, pre["coefficient"].to_numpy()),
            "two_sided_placebo_p": randomization_pvalue(observed, pre["coefficient"].to_numpy()),
            "placebo_years": len(pre),
        })
        fig, ax = plt.subplots(figsize=(8.5, 5.2))
        ax.hist(pre["coefficient"].dropna(), bins=min(15, max(5, len(pre) // 2)))
        ax.axvline(observed, linestyle="--", linewidth=1.5, label="2026")
        ax.set_title(f"Historical pseudo-shock distribution: {outcome}")
        ax.set_xlabel("Exposure × post coefficient")
        ax.legend()
        fig.tight_layout()
        fig.savefig(paths["figures"] / f"historical_placebo_{outcome}.png", dpi=180)
        plt.close(fig)
    summary = pd.DataFrame(summary_rows)
    placebo.to_csv(paths["tables"] / "historical_pseudo_shock_coefficients.csv", index=False)
    summary.to_csv(paths["tables"] / "historical_placebo_summary.csv", index=False)
    return placebo, summary


def origin_randomization(focal: pd.DataFrame, paths: dict[str, Path], draws: int = 999) -> pd.DataFrame:
    rng = np.random.default_rng(26022026)
    data = focal[(~focal["intra_eu"].astype(bool)) & focal["month"].between("2023-01-01", focal["month"].max())].copy()
    data["calendar_month"] = data["month"].dt.month
    rows = []
    for product in FOCAL:
        p = data[data["hs6"].eq(product)].copy()
        baseline = p[p["month"].between("2023-01-01", "2025-12-01")].groupby(["partner", "calendar_month"], as_index=False).agg(base_q=("quantity_kg", "mean"))
        post = p[p["month"].ge(SHOCK)].groupby(["partner", "calendar_month"], as_index=False).agg(post_q=("quantity_kg", "sum"), post_months=("month", "nunique"))
        post["post_q"] = post["post_q"] / post["post_months"].replace(0, np.nan)
        change = baseline.merge(post, on=["partner", "calendar_month"], how="outer").fillna(0)
        change = change.groupby("partner", as_index=False).agg(base_q=("base_q", "sum"), post_q=("post_q", "sum"))
        change["log_change"] = np.log1p(change["post_q"] / 1000) - np.log1p(change["base_q"] / 1000)
        eligible = change[change["base_q"].gt(change["base_q"].quantile(0.20))].copy()
        treated = eligible[eligible["partner"].isin(HIGH_ROUTE)]
        if treated.empty:
            rows.append({"hs6": product, "status": "no_treated_origins"})
            continue
        observed = np.average(treated["log_change"], weights=np.maximum(treated["base_q"], 1))
        donors = eligible[~eligible["partner"].isin(ALL_ROUTE | NEARBY_NON_HORMUZ)].copy()
        n_treated = len(treated)
        placebo = []
        probabilities = np.sqrt(np.maximum(donors["base_q"].to_numpy(), 1))
        probabilities = probabilities / probabilities.sum()
        for _ in range(draws):
            if len(donors) < n_treated:
                break
            indices = rng.choice(len(donors), size=n_treated, replace=False, p=probabilities)
            sample = donors.iloc[indices]
            placebo.append(np.average(sample["log_change"], weights=np.maximum(sample["base_q"], 1)))
        rows.append({
            "hs6": product, "product_label": FOCAL[product], "status": "ok",
            "treated_origins": "|".join(sorted(treated["partner"])), "treated_count": n_treated,
            "observed_weighted_log_change": observed, "randomization_p_two_sided": randomization_pvalue(observed, np.array(placebo)),
            "randomization_percentile": percentile_rank(observed, np.array(placebo)), "draws": len(placebo),
            "placebo_mean": np.mean(placebo) if placebo else np.nan, "placebo_sd": np.std(placebo, ddof=1) if len(placebo) > 1 else np.nan,
        })
        if placebo:
            fig, ax = plt.subplots(figsize=(8.5, 5.2))
            ax.hist(placebo, bins=30)
            ax.axvline(observed, linestyle="--", linewidth=1.5, label="Route-exposed origins")
            ax.set_title(f"Origin-label randomization: {FOCAL[product]}")
            ax.set_xlabel("Weighted post-shock log quantity change")
            ax.legend()
            fig.tight_layout()
            fig.savefig(paths["figures"] / f"origin_randomization_{product}.png", dpi=180)
            plt.close(fig)
    result = pd.DataFrame(rows)
    result.to_csv(paths["tables"] / "origin_randomization_inference.csv", index=False)
    return result


def matched_product_controls(network: pd.DataFrame, focal: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    pre = network[network["month"].between("2019-01-01", "2025-12-01")].copy()
    pre["log_uv"] = np.log(pre["unit_value_eur_per_kg"].where(pre["unit_value_eur_per_kg"].gt(0)))
    characteristics = pre.groupby("hs6", as_index=False).agg(
        total_value_eur=("total_value_eur", "sum"), total_quantity_kg=("total_quantity_kg", "sum"),
        mean_hhi=("supplier_hhi", "mean"), mean_suppliers=("supplier_count", "mean"),
        unit_value_volatility=("log_uv", "std"), mean_distance_km=("weighted_distance_km", "mean"),
        countries=("reporter", "nunique"), months=("month", "nunique"),
    )
    route = focal[focal["month"].between("2019-01-01", "2025-12-01")].copy()
    route["route_q"] = route["quantity_kg"].fillna(0) * route["high_route"]
    route_exp = route.groupby("hs6", as_index=False).agg(route_q=("route_q", "sum"), total_q=("quantity_kg", "sum"))
    route_exp["route_share"] = np.where(route_exp["total_q"].gt(0), route_exp["route_q"] / route_exp["total_q"], 0)
    characteristics = characteristics.merge(route_exp[["hs6", "route_share"]], on="hs6", how="left").fillna({"route_share": 0})
    characteristics["chapter"] = characteristics["hs6"].str[:2]
    features = ["total_value_eur", "mean_hhi", "mean_suppliers", "unit_value_volatility", "mean_distance_km"]
    characteristics["log_total_value"] = np.log1p(characteristics["total_value_eur"])
    features[0] = "log_total_value"
    matches = []
    for focal_product in FOCAL:
        target = characteristics[characteristics["hs6"].eq(focal_product)]
        if target.empty:
            continue
        candidates = characteristics[
            characteristics["chapter"].eq(focal_product[:2])
            & ~characteristics["hs6"].isin(FOCAL)
            & characteristics["route_share"].lt(0.01)
            & characteristics["months"].ge(60)
        ].copy()
        universe = pd.concat([target, candidates], ignore_index=True)
        z = universe[features].copy()
        z = (z - z.mean()) / z.std().replace(0, 1)
        target_vector = z.iloc[0]
        candidates["match_distance"] = np.sqrt(((z.iloc[1:].reset_index(drop=True) - target_vector) ** 2).sum(axis=1))
        for rank, (_, row) in enumerate(candidates.nsmallest(10, "match_distance").iterrows(), 1):
            matches.append({"focal_hs6": focal_product, "focal_label": FOCAL[focal_product], "control_hs6": row["hs6"], "rank": rank, "match_distance": row["match_distance"], **{feature: row[feature] for feature in features}, "route_share": row["route_share"]})
    result = pd.DataFrame(matches)
    result.to_csv(paths["tables"] / "matched_product_controls.csv", index=False)
    characteristics.to_csv(paths["data"] / "hs6_prewar_product_characteristics.csv", index=False)
    return result


def load_portwatch(supplementary_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    daily_path = find_csv(supplementary_root, ["portwatch", "daily", "baselines"])
    dictionary_path = find_csv(supplementary_root, ["portwatch", "dictionary"])
    if daily_path is None:
        return pd.DataFrame(), pd.DataFrame(), {"status": "missing"}
    daily = read_table(daily_path)
    dictionary = read_table(dictionary_path) if dictionary_path else pd.DataFrame()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["portid"] = daily["portid"].astype(str).str.lower()
    if "portname" not in daily and not dictionary.empty:
        dictionary["portid"] = dictionary["portid"].astype(str).str.lower()
        daily = daily.merge(dictionary[["portid", "portname"]], on="portid", how="left")
    return daily, dictionary, {"status": "ok", "path": str(daily_path), "rows": len(daily), "first_date": str(daily["date"].min().date()), "latest_date": str(daily["date"].max().date())}


def synthetic_control_shipping(daily: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    if daily.empty:
        return pd.DataFrame(), pd.DataFrame()
    name = daily.get("portname", pd.Series("", index=daily.index)).fillna("").astype(str)
    hormuz_ids = daily.loc[name.str.contains("hormuz", case=False, regex=False), "portid"].unique().tolist()
    if hormuz_ids:
        treated_id = hormuz_ids[0]
        selection = "name_match"
    else:
        pre = daily[daily["date"].between("2025-03-01", "2026-02-27")].groupby("portid")["n_total"].mean()
        post = daily[daily["date"].between("2026-03-01", "2026-03-31")].groupby("portid")["n_total"].mean()
        ratio = (post / pre).sort_values()
        treated_id = ratio.index[0]
        selection = "largest_march_collapse_fallback"
    outcomes = [column for column in ["n_total", "n_tanker", "n_cargo", "capacity"] if column in daily]
    result_rows = []
    weight_rows = []
    daily = daily[daily["date"].between("2025-03-01", daily["date"].max())].copy()
    for outcome in outcomes:
        pivot = daily.pivot_table(index="date", columns="portid", values=outcome, aggfunc="mean").sort_index()
        if treated_id not in pivot:
            continue
        donors = [column for column in pivot.columns if column != treated_id]
        pre_mask = pivot.index < pd.Timestamp("2026-03-01")
        valid_donors = [column for column in donors if pivot.loc[pre_mask, column].notna().mean() >= 0.9]
        matrix = pivot.loc[pre_mask, valid_donors].interpolate().ffill().bfill().to_numpy(float)
        target = pivot.loc[pre_mask, treated_id].interpolate().ffill().bfill().to_numpy(float)
        scale = np.nanstd(target) or 1.0
        matrix_scaled = matrix / scale
        target_scaled = target / scale
        n = len(valid_donors)
        objective = lambda w: float(np.mean((target_scaled - matrix_scaled @ w) ** 2))
        res = optimize.minimize(objective, np.full(n, 1 / n), method="SLSQP", bounds=[(0, 1)] * n, constraints={"type": "eq", "fun": lambda w: w.sum() - 1})
        weights = res.x if res.success else np.full(n, 1 / n)
        full_matrix = pivot[valid_donors].interpolate().ffill().bfill().to_numpy(float)
        synthetic = full_matrix @ weights
        treated = pivot[treated_id].interpolate().ffill().bfill().to_numpy(float)
        gap = treated - synthetic
        series = pd.DataFrame({"date": pivot.index, "treated": treated, "synthetic": synthetic, "gap": gap})
        series["outcome"] = outcome
        series["treated_portid"] = treated_id
        series.to_csv(paths["data"] / f"shipping_synthetic_series_{outcome}.csv", index=False)
        pre_rmspe = float(np.sqrt(np.mean(gap[pre_mask] ** 2)))
        windows = {
            "march": ("2026-03-01", "2026-03-31"),
            "april_may": ("2026-04-01", "2026-05-31"),
            "june_july": ("2026-06-01", str(pivot.index.max().date())),
        }
        for window, (start, end) in windows.items():
            mask = (pivot.index >= pd.Timestamp(start)) & (pivot.index <= pd.Timestamp(end))
            if not mask.any():
                continue
            result_rows.append({
                "outcome": outcome, "window": window, "treated_portid": treated_id,
                "treated_selection": selection, "mean_treated": float(np.mean(treated[mask])),
                "mean_synthetic": float(np.mean(synthetic[mask])), "mean_gap": float(np.mean(gap[mask])),
                "relative_gap": float(np.mean(gap[mask]) / np.mean(synthetic[mask])) if np.mean(synthetic[mask]) != 0 else np.nan,
                "cumulative_gap": float(np.sum(gap[mask])), "pre_rmspe": pre_rmspe,
                "post_rmspe_ratio": float(np.sqrt(np.mean(gap[mask] ** 2)) / pre_rmspe) if pre_rmspe > 0 else np.nan,
            })
        for donor, weight in zip(valid_donors, weights):
            weight_rows.append({"outcome": outcome, "treated_portid": treated_id, "donor_portid": donor, "weight": weight})

        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.plot(pivot.index, treated, label="Hormuz")
        ax.plot(pivot.index, synthetic, label="Synthetic control")
        ax.axvline(pd.Timestamp("2026-03-01"), linestyle="--", linewidth=0.8)
        ax.set_title(f"Daily {outcome}: Hormuz and synthetic control")
        ax.legend()
        fig.tight_layout()
        fig.savefig(paths["figures"] / f"shipping_synthetic_{outcome}.png", dpi=180)
        plt.close(fig)

    results = pd.DataFrame(result_rows)
    weights_frame = pd.DataFrame(weight_rows)
    # Space-placebo RMSPE ratios using the same pre/post March window.
    placebo_rows = []
    if outcomes:
        outcome = "n_tanker" if "n_tanker" in outcomes else outcomes[0]
        pivot = daily.pivot_table(index="date", columns="portid", values=outcome, aggfunc="mean").sort_index()
        pre_mask = pivot.index < pd.Timestamp("2026-03-01")
        post_mask = pivot.index.between(pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-31"))
        for pseudo in pivot.columns:
            donors = [column for column in pivot.columns if column != pseudo and pivot.loc[pre_mask, column].notna().mean() >= 0.9]
            if len(donors) < 2 or pivot.loc[pre_mask, pseudo].notna().mean() < 0.9:
                continue
            X = pivot.loc[pre_mask, donors].interpolate().ffill().bfill().to_numpy(float)
            y = pivot.loc[pre_mask, pseudo].interpolate().ffill().bfill().to_numpy(float)
            n = len(donors)
            res = optimize.minimize(lambda w: np.mean((y - X @ w) ** 2), np.full(n, 1 / n), method="SLSQP", bounds=[(0, 1)] * n, constraints={"type": "eq", "fun": lambda w: w.sum() - 1})
            w = res.x if res.success else np.full(n, 1 / n)
            synth = pivot[donors].interpolate().ffill().bfill().to_numpy(float) @ w
            actual = pivot[pseudo].interpolate().ffill().bfill().to_numpy(float)
            gap = actual - synth
            pre_rmspe = np.sqrt(np.mean(gap[pre_mask] ** 2))
            post_rmspe = np.sqrt(np.mean(gap[post_mask] ** 2))
            placebo_rows.append({"portid": pseudo, "outcome": outcome, "rmspe_ratio": post_rmspe / pre_rmspe if pre_rmspe > 0 else np.nan, "mean_march_gap": np.mean(gap[post_mask]), "is_hormuz": pseudo == treated_id})
    placebos = pd.DataFrame(placebo_rows)
    if not placebos.empty:
        observed = placebos.loc[placebos["is_hormuz"], "rmspe_ratio"].iloc[0]
        p = (1 + (placebos["rmspe_ratio"] >= observed).sum()) / (1 + len(placebos))
        placebos["space_placebo_p"] = p
    results.to_csv(paths["tables"] / "shipping_synthetic_control_results.csv", index=False)
    weights_frame.to_csv(paths["tables"] / "shipping_synthetic_control_weights.csv", index=False)
    placebos.to_csv(paths["tables"] / "shipping_space_placebos.csv", index=False)
    return results, placebos


def load_pink_sheet(supplementary_root: Path, paths: dict[str, Path]) -> tuple[pd.DataFrame, dict]:
    path = find_csv(supplementary_root, ["pink", "monthly", "long"])
    if path is None:
        return pd.DataFrame(), {"status": "missing"}
    frame = read_table(path)
    frame["month"] = pd.to_datetime(frame["month"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    text = (frame.get("series", "").astype(str) + " " + frame.get("commodity_name", "").astype(str)).str.lower()
    selected = frame[text.str.contains(r"alumin|urea|natural gas|crude oil|dap|tsp|phosphate", regex=True)].copy()
    selected.to_csv(paths["data"] / "selected_world_bank_benchmark_prices.csv", index=False)
    return selected, {"status": "ok", "path": str(path), "rows": len(frame), "selected_rows": len(selected), "series": selected.get("series", pd.Series(dtype=str)).nunique()}


def merge_benchmark_adjustment(network_sample: pd.DataFrame, benchmarks: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    if benchmarks.empty:
        return pd.DataFrame()
    candidates = benchmarks[["month", "series", "commodity_name", "value"]].copy()
    candidates["label"] = (candidates["series"].astype(str) + " " + candidates["commodity_name"].astype(str)).str.lower()
    aluminum_series = candidates[candidates["label"].str.contains("alumin")]["series"].drop_duplicates().tolist()
    urea_series = candidates[candidates["label"].str.contains("urea")]["series"].drop_duplicates().tolist()
    gas_series = candidates[candidates["label"].str.contains("natural gas")]["series"].drop_duplicates().tolist()
    mappings = {
        "281410": (urea_series[:1] + gas_series[:1]),
        "760110": aluminum_series[:1],
        "760120": aluminum_series[:1],
    }
    rows = []
    for product, series_list in mappings.items():
        if not series_list:
            continue
        subset = candidates[candidates["series"].isin(series_list)].groupby("month", as_index=False).agg(benchmark_value=("value", "mean"))
        subset["benchmark_log"] = np.log(subset["benchmark_value"].where(subset["benchmark_value"].gt(0)))
        product_data = network_sample[network_sample["hs6"].eq(product)].merge(subset[["month", "benchmark_log"]], on="month", how="left")
        product_data["benchmark_adjusted_log_unit_value"] = product_data["log_unit_value"] - product_data["benchmark_log"]
        rows.append(product_data)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not out.empty:
        out.to_parquet(paths["data"] / "benchmark_adjusted_import_prices.parquet", index=False, compression="zstd")
    return out


def inspect_and_analyse_sts(sts_root: Path, exposure: pd.DataFrame, paths: dict[str, Path]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    audits = []
    candidates = []
    for path in sts_root.rglob("*"):
        if path.is_file() and (path.suffix in {".csv", ".gz", ".parquet"} or path.name.endswith(".csv.gz")):
            try:
                frame = read_table(path)
                audits.append({"path": str(path), "rows": len(frame), "columns": "|".join(map(str, frame.columns))})
                lower = {str(column).lower(): column for column in frame.columns}
                if any(key in lower for key in ["geo", "country", "reporter"]) and any(key in lower for key in ["value", "obs_value"]):
                    candidates.append((path, frame))
            except Exception as exc:
                audits.append({"path": str(path), "error": repr(exc)})
    pd.DataFrame(audits).to_csv(paths["audit"] / "sts_file_audit.csv", index=False)
    standardized = []
    for path, frame in candidates:
        lower = {str(column).lower(): column for column in frame.columns}
        geo = lower.get("geo") or lower.get("country") or lower.get("reporter")
        time = lower.get("month") or lower.get("time") or lower.get("time_period")
        value = lower.get("value") or lower.get("obs_value")
        sector = lower.get("nace_r2") or lower.get("nace") or lower.get("activity") or lower.get("indic")
        dataset = lower.get("dataset")
        if not all([geo, time, value]):
            continue
        x = pd.DataFrame({
            "country": frame[geo].astype(str).str.upper(),
            "month": pd.to_datetime(frame[time], errors="coerce").dt.to_period("M").dt.to_timestamp(),
            "value": pd.to_numeric(frame[value], errors="coerce"),
            "sector": frame[sector].astype(str) if sector else path.stem,
            "dataset": frame[dataset].astype(str) if dataset else path.stem,
        })
        x = x[x["country"].isin(EU27) & x["month"].notna() & x["value"].notna()]
        standardized.append(x)
    sts = pd.concat(standardized, ignore_index=True) if standardized else pd.DataFrame()
    if sts.empty:
        return sts, pd.DataFrame(), {"status": "unavailable_or_unparsed", "files": len(audits)}
    sts = sts.drop_duplicates(["country", "month", "sector", "dataset"], keep="last")
    sts.to_parquet(paths["data"] / "eurostat_sts_standardized.parquet", index=False, compression="zstd")
    # Map the most relevant available sector labels.  Detailed C2015/C2442 are
    # preferred; C20/C24 are retained when detailed series are unavailable.
    sector_text = sts["sector"].str.upper()
    ammonia_sector = np.where(sector_text.str.contains("2015"), "ammonia_downstream", np.where(sector_text.str.contains(r"(^|[^0-9])20([^0-9]|$)", regex=True), "chemicals", None))
    aluminum_sector = np.where(sector_text.str.contains("2442"), "aluminum_downstream", np.where(sector_text.str.contains(r"(^|[^0-9])24([^0-9]|$)", regex=True), "basic_metals", None))
    sts["mapped_sector"] = pd.Series(ammonia_sector, index=sts.index).fillna(pd.Series(aluminum_sector, index=sts.index))
    sts = sts[sts["mapped_sector"].notna()].copy()
    if sts.empty:
        return sts, pd.DataFrame(), {"status": "no_relevant_sector_codes", "files": len(audits)}
    exp = exposure.copy()
    exp["mapped_sector"] = np.where(exp["hs6"].eq("281410"), "ammonia_downstream", np.where(exp["hs6"].str.startswith("7601"), "aluminum_downstream", None))
    exp = exp.groupby(["reporter", "mapped_sector"], as_index=False).agg(route_exposure=("route_exposure", "max"))
    exp = pd.concat([
        exp,
        exp.assign(mapped_sector=exp["mapped_sector"].replace({"ammonia_downstream": "chemicals", "aluminum_downstream": "basic_metals"}))
    ]).drop_duplicates(["reporter", "mapped_sector"])
    analysis = sts.merge(exp, left_on=["country", "mapped_sector"], right_on=["reporter", "mapped_sector"], how="left")
    analysis["route_exposure"] = analysis["route_exposure"].fillna(0)
    analysis = analysis[analysis["month"].between("2024-01-01", analysis["month"].max())]
    analysis["post"] = analysis["month"].ge(SHOCK).astype("int8")
    analysis["exposure_post"] = analysis["route_exposure"] * analysis["post"]
    analysis["log_index"] = np.log(analysis["value"].where(analysis["value"].gt(0)))
    analysis["country_sector_fe"] = analysis["country"] + "|" + analysis["mapped_sector"] + "|" + analysis["dataset"]
    analysis["country_month_fe"] = analysis["country"] + "|" + analysis["month"].astype(str)
    analysis["sector_month_fe"] = analysis["mapped_sector"] + "|" + analysis["dataset"] + "|" + analysis["month"].astype(str)
    rows = []
    for dataset in analysis["dataset"].unique():
        sample = analysis[analysis["dataset"].eq(dataset)]
        if sample["country"].nunique() < 8 or sample["month"].nunique() < 12:
            continue
        try:
            fit = fe_ols(sample, "log_index", ["exposure_post"], ["country_sector_fe", "country_month_fe", "sector_month_fe"], "country")
            row = fit.frame().iloc[0].to_dict()
            row.update({"dataset": dataset, "model": "downstream_sts", "latest_month": sample["month"].max()})
            rows.append(row)
        except Exception as exc:
            rows.append({"dataset": dataset, "model": "downstream_sts", "error": repr(exc)})
    results = pd.DataFrame(rows)
    results.to_csv(paths["tables"] / "downstream_sts_models.csv", index=False)
    return analysis, results, {"status": "ok", "files": len(audits), "rows": len(analysis), "datasets": analysis["dataset"].nunique(), "latest_month": str(analysis["month"].max().date())}


def inspect_comtrade(comtrade_root: Path, paths: dict[str, Path]) -> dict:
    audits = []
    for path in comtrade_root.rglob("*"):
        if path.is_file():
            record = {"path": str(path), "bytes": path.stat().st_size}
            if path.suffix in {".csv", ".gz", ".json"} or path.name.endswith(".csv.gz"):
                try:
                    if path.suffix == ".json":
                        payload = json.loads(path.read_text())
                        record["json_type"] = type(payload).__name__
                        record["keys"] = list(payload)[:20] if isinstance(payload, dict) else None
                    else:
                        frame = read_table(path)
                        record["rows"] = len(frame)
                        record["columns"] = frame.columns.tolist()
                except Exception as exc:
                    record["error"] = repr(exc)
            audits.append(record)
    (paths["audit"] / "comtrade_validation_audit.json").write_text(json.dumps(audits, indent=2, default=str))
    usable = any(record.get("rows", 0) > 0 for record in audits)
    return {"status": "usable" if usable else "not_usable", "files": len(audits), "records": audits}


def robustness_grid(measures: pd.DataFrame, exposure: pd.DataFrame, paths: dict[str, Path]) -> pd.DataFrame:
    base = measures.merge(exposure, on=["reporter", "hs6"], how="left")
    base["product_label"] = base["hs6"].map(FOCAL)
    base["log_quantity"] = np.log1p(base["total_quantity_kg"] / 1000)
    base["log_unit_value"] = np.log(base["unit_value_eur_per_kg"].where(base["unit_value_eur_per_kg"].gt(0)))
    rows = []
    specifications = []
    for start in ["2023-01-01", "2024-01-01"]:
        for end in ["2026-04-01", str(base["month"].max().date())]:
            for exposure_col in ["route_exposure", "narrow_route_exposure"]:
                for exclude_hubs in [False, True]:
                    specifications.append((start, end, exposure_col, exclude_hubs))
    for start, end, exposure_col, exclude_hubs in specifications:
        sample = base[base["month"].between(start, end)].copy()
        if exclude_hubs:
            sample = sample[~sample["reporter"].isin({"BE", "NL"})]
        sample["post"] = sample["month"].ge(SHOCK).astype("int8")
        sample["treatment"] = sample[exposure_col].fillna(0) * sample["post"]
        sample["ip_fe"] = sample["reporter"] + "|" + sample["hs6"]
        sample["im_fe"] = sample["reporter"] + "|" + sample["month"].astype(str)
        sample["pm_fe"] = sample["hs6"] + "|" + sample["month"].astype(str)
        for outcome in ["log_quantity", "log_unit_value", "supplier_hhi", "weighted_distance_km", "entrant_quantity_share"]:
            try:
                fit = fe_ols(sample, outcome, ["treatment"], ["ip_fe", "im_fe", "pm_fe"], "reporter")
                row = fit.frame().iloc[0].to_dict()
                row.update({"start": start, "end": end, "exposure_definition": exposure_col, "exclude_be_nl": exclude_hubs, "outcome": outcome})
                rows.append(row)
            except Exception as exc:
                rows.append({"start": start, "end": end, "exposure_definition": exposure_col, "exclude_be_nl": exclude_hubs, "outcome": outcome, "error": repr(exc)})
    result = pd.DataFrame(rows)
    result.to_csv(paths["tables"] / "robustness_specification_grid.csv", index=False)
    return result


def make_report(paths: dict[str, Path], audit: dict, tables: dict[str, pd.DataFrame]) -> None:
    def markdown(frame: pd.DataFrame, columns: list[str] | None = None, n: int = 30) -> str:
        if frame is None or frame.empty:
            return "_No estimable result._"
        view = frame.copy()
        if columns:
            view = view[[column for column in columns if column in view]]
        return view.head(n).to_markdown(index=False, floatfmt=".4g")

    shipping = tables.get("shipping")
    route = tables.get("route")
    network = tables.get("network")
    replacement = tables.get("replacement")
    placebos = tables.get("placebo_summary")
    randomization = tables.get("randomization")
    downstream = tables.get("downstream")
    report = f"""# Paper 2 publication-dataset analysis

## Empirical question

How did the 2026 closure of the Strait of Hormuz alter European imports of energy-intensive intermediates, and through which margins did markets adjust: trade destruction, incumbent-supplier expansion, supplier entry, geographic rerouting, concentration, and import prices?

## Data coverage

```json
{json.dumps(audit, indent=2, default=str)}
```

## Identification architecture

1. **Independent physical first stage.** Daily Hormuz traffic is compared with a synthetic combination of seven other chokepoints, with space placebos.
2. **Bilateral route first stage.** Importer-origin-product relationships are balanced to retain zeros. Models absorb importer-origin-product and importer-product-month fixed effects, so route-exposed origins are compared with other origins supplying the same product to the same importer in the same month.
3. **Market reorganization.** Country-product outcomes are related to prewar route exposure with country-product, country-month and product-month fixed effects.
4. **Mechanisms.** Lost route-exposed quantities are decomposed into incumbent expansion, new-supplier entry and unreplaced shortfall. Supplier concentration, entry, exit, and distance are measured directly.
5. **Historical falsification.** The exact country-product exposure design is repeated using March pseudo-shocks in every feasible pre-2026 year.
6. **Inference.** Standard errors are clustered at the assignment level; origin-label randomization and chokepoint space-placebo p-values are reported because conventional asymptotics are fragile with few physically treated origins.

## Shipping first stage

{markdown(shipping, ["outcome", "window", "relative_gap", "cumulative_gap", "post_rmspe_ratio", "pre_rmspe"])}

## Bilateral route effects

{markdown(route, ["model", "definition", "product", "outcome", "coefficient", "std_error", "p_value", "n_obs", "clusters"])}

## Market-reorganization effects

{markdown(network, ["model", "sample", "outcome", "treatment", "coefficient", "std_error", "p_value", "n_obs", "clusters"])}

## Replacement decomposition

{markdown(replacement)}

## Origin-label randomization inference

{markdown(randomization)}

## Historical pseudo-shock distribution

{markdown(placebos)}

## Downstream producer-price and industrial-production tests

{markdown(downstream)}

## Interpretation rules

- A product enters the principal causal sample only when the independent shipping first stage and the bilateral route first stage point in the expected direction.
- Unit-value results are not interpreted as pure prices unless they survive the fixed-supplier price index and benchmark-adjusted specifications.
- Downstream results are labelled exploratory because only a short post-shock window is currently available.
- Results that are not extreme relative to the historical pseudo-shock distribution are not presented as effects of the 2026 war.

## Files

Exact estimates, event-study coefficients, placebo distributions, the focal bilateral panel, the country-product network panel, and all data audits are included in the accompanying package.
"""
    (paths["root"] / "EMPIRICAL_ANALYSIS_REPORT.md").write_text(report, encoding="utf-8")


def build_sqlite(paths: dict[str, Path], tables: dict[str, pd.DataFrame]) -> None:
    db = paths["root"] / "paper2_publication_database.sqlite"
    with sqlite3.connect(db) as connection:
        for name, frame in tables.items():
            if frame is not None and not frame.empty:
                clean_name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:60]
                frame.to_sql(clean_name, connection, if_exists="replace", index=False, chunksize=25_000)


def package_outputs(paths: dict[str, Path], script_path: Path) -> None:
    shutil.copy2(script_path, paths["scripts"] / script_path.name)
    helper = script_path.parent / "econometrics.py"
    if helper.exists():
        shutil.copy2(helper, paths["scripts"] / helper.name)
    manifest = []
    for path in sorted(paths["root"].rglob("*")):
        if path.is_file() and path.name != "paper2_publication_analysis_package.zip":
            manifest.append({"path": str(path.relative_to(paths["root"])), "bytes": path.stat().st_size, "sha256": sha256(path)})
    pd.DataFrame(manifest).to_csv(paths["root"] / "FILE_MANIFEST.csv", index=False)
    package = paths["root"] / "paper2_publication_analysis_package.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(paths["root"].rglob("*")):
            if path.is_file() and path != package:
                archive.write(path, path.relative_to(paths["root"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comext-root", type=Path, required=True)
    parser.add_argument("--supplementary-root", type=Path, required=True)
    parser.add_argument("--sts-root", type=Path, required=True)
    parser.add_argument("--comtrade-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    paths = mkdirs(args.output_root)
    audit: dict = {"generated_at_utc": pd.Timestamp.utcnow().isoformat(), "sources": {}}

    distance, distance_audit = load_distance(args.supplementary_root)
    audit["sources"]["cepii_geodist"] = distance_audit
    focal, network, origin_month, coverage, trade_audit = build_trade_panels(args.comext_root, distance, paths)
    audit["sources"]["comext"] = trade_audit
    measures, established = add_entry_exit_measures(focal, network)
    exposure = prewar_exposure(focal)
    exposure.to_csv(paths["data"] / "prewar_route_exposure_2023_2025.csv", index=False)
    balanced = build_balanced_relationship_panel(focal, MAIN_START, focal["month"].max())
    balanced.to_parquet(paths["data"] / "balanced_focal_relationship_month.parquet", index=False, compression="zstd")

    route_results, route_events, route_audit = route_trade_models(balanced, paths)
    audit["models"] = {"route": route_audit}
    network_results, network_events = network_models(measures, exposure, paths)
    decomposition, replacement = replacement_decomposition(focal, established, paths)
    exit_results = relationship_exit_model(focal, established, paths)
    price_components = price_decomposition(focal, paths)
    placebo_coefficients, placebo_summary = historical_placebos(focal, network, paths)
    randomization = origin_randomization(focal, paths)
    matches = matched_product_controls(network, focal, paths)
    robustness = robustness_grid(measures, exposure, paths)

    portwatch, port_dictionary, port_audit = load_portwatch(args.supplementary_root)
    audit["sources"]["portwatch"] = port_audit
    shipping_results, shipping_placebos = synthetic_control_shipping(portwatch, paths)
    benchmarks, benchmark_audit = load_pink_sheet(args.supplementary_root, paths)
    audit["sources"]["world_bank_pink_sheet"] = benchmark_audit
    benchmark_adjusted = merge_benchmark_adjustment(
        measures.merge(exposure, on=["reporter", "hs6"], how="left").assign(
            log_unit_value=lambda x: np.log(x["unit_value_eur_per_kg"].where(x["unit_value_eur_per_kg"].gt(0)))
        ), benchmarks, paths
    )
    sts_analysis, downstream_results, sts_audit = inspect_and_analyse_sts(args.sts_root, exposure, paths)
    audit["sources"]["eurostat_sts"] = sts_audit
    comtrade_audit = inspect_comtrade(args.comtrade_root, paths)
    audit["sources"]["un_comtrade_preview"] = comtrade_audit

    tables = {
        "route_trade_models": route_results,
        "route_trade_event_study": route_events,
        "network_reorganization_models": network_results,
        "network_reorganization_event_study": network_events,
        "replacement_country_month": decomposition,
        "replacement_product": replacement,
        "relationship_exit_models": exit_results,
        "price_decomposition": price_components,
        "historical_placebo_coefficients": placebo_coefficients,
        "historical_placebo_summary": placebo_summary,
        "origin_randomization": randomization,
        "matched_product_controls": matches,
        "robustness_grid": robustness,
        "shipping_results": shipping_results,
        "shipping_placebos": shipping_placebos,
        "downstream_results": downstream_results,
        "prewar_exposure": exposure,
    }
    build_sqlite(paths, tables)
    report_tables = {
        "shipping": shipping_results,
        "route": route_results,
        "network": network_results,
        "replacement": replacement,
        "placebo_summary": placebo_summary,
        "randomization": randomization,
        "downstream": downstream_results,
    }
    audit["outputs"] = {
        "focal_rows": len(focal), "balanced_relationship_rows": len(balanced),
        "network_rows": len(network), "prewar_exposure_rows": len(exposure),
        "latest_trade_month": str(focal["month"].max().date()),
        "shipping_rows": len(portwatch), "sts_rows": len(sts_analysis),
    }
    (paths["root"] / "BUILD_AND_ANALYSIS_AUDIT.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    make_report(paths, audit, report_tables)
    package_outputs(paths, Path(__file__).resolve())
    print(json.dumps(audit["outputs"], indent=2), flush=True)


if __name__ == "__main__":
    main()
