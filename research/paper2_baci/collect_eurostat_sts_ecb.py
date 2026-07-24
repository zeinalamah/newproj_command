#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

OUT = Path('research_output/sts_ecb')
OUT.mkdir(parents=True, exist_ok=True)
EU27 = {'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
TARGET_GEOS = EU27 | {'EU27_2020', 'EA20'}
TARGET_NACE_PREFIXES = ('C20', 'C24')


def fetch_with_retry(url: str, path: Path, timeout: int = 900) -> dict:
    last_error = None
    with requests.Session() as session:
        session.headers.update({'User-Agent': 'academic-hormuz-energy-research/1.0'})
        for attempt in range(12):
            try:
                response = session.get(url, timeout=timeout)
                if response.status_code in {202, 429, 500, 502, 503, 504}:
                    last_error = f'HTTP {response.status_code}: {response.text[:500]}'
                    time.sleep(min(120, 5 * (attempt + 1)))
                    continue
                response.raise_for_status()
                path.write_bytes(response.content)
                return {'url': url, 'status': 'ok', 'bytes': path.stat().st_size, 'attempts': attempt + 1}
            except requests.RequestException as exc:
                last_error = repr(exc)
                if attempt == 11:
                    break
                time.sleep(min(120, 2 ** attempt))
    return {'url': url, 'status': 'failed', 'error': last_error}


def parse_eurostat_tsv(path: Path, dataset: str) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, sep='\t', dtype=str, low_memory=False)
    key_col = raw.columns[0]
    dimensions = key_col.split('\\')[0].split(',')
    keys = raw[key_col].str.split(',', expand=True)
    if keys.shape[1] != len(dimensions):
        raise RuntimeError(f'{dataset}: expected {len(dimensions)} key dimensions but found {keys.shape[1]}')
    keys.columns = dimensions
    series = pd.concat([keys, raw.drop(columns=[key_col])], axis=1)
    if 'geo' not in series or 'nace_r2' not in series:
        raise RuntimeError(f'{dataset}: unexpected dimensions {dimensions}')
    series = series.loc[
        series['geo'].isin(TARGET_GEOS)
        & series['nace_r2'].fillna('').str.startswith(TARGET_NACE_PREFIXES)
    ].copy()
    id_vars = dimensions
    time_cols = [c for c in series.columns if c not in id_vars]
    long = series.melt(id_vars=id_vars, value_vars=time_cols, var_name='time', value_name='raw_observation')
    long['month'] = pd.to_datetime(long['time'].astype(str).str.strip(), format='%Y-%m', errors='coerce')
    long = long.loc[long['month'].notna() & long['month'].ge('2000-01-01')].copy()
    extracted = long['raw_observation'].fillna('').astype(str).str.strip().str.extract(
        r'^(?P<value>-?(?:\d+(?:\.\d*)?|\.\d+))?(?:\s+(?P<status>.*))?$'
    )
    long['value'] = pd.to_numeric(extracted['value'], errors='coerce')
    long['status'] = extracted['status'].fillna('').str.strip()
    long = long.loc[long['value'].notna()].copy()
    keep = dimensions + ['month', 'value', 'status', 'raw_observation']
    long = long[keep].sort_values(['geo', 'nace_r2', 'month'] + [c for c in dimensions if c not in {'geo','nace_r2'}])
    summary = {
        'dataset': dataset,
        'rows': int(len(long)),
        'first_month': str(long['month'].min().date()) if len(long) else None,
        'latest_month': str(long['month'].max().date()) if len(long) else None,
        'geos': sorted(long['geo'].unique().tolist()),
        'nace_codes': sorted(long['nace_r2'].unique().tolist()),
        'dimensions': dimensions,
        'dimension_values': {c: sorted(long[c].dropna().unique().tolist()) for c in dimensions if c not in {'geo'}},
    }
    return long, summary


def parse_ecb(path: Path) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path)
    cols = {c.upper(): c for c in raw.columns}
    time_col = cols.get('TIME_PERIOD') or cols.get('TIME_PERIOD_START')
    value_col = cols.get('OBS_VALUE')
    if not time_col or not value_col:
        raise RuntimeError(f'Unexpected ECB columns: {raw.columns.tolist()}')
    out = pd.DataFrame({
        'month': pd.to_datetime(raw[time_col], errors='coerce'),
        'usd_per_eur': pd.to_numeric(raw[value_col], errors='coerce'),
    }).dropna().sort_values('month')
    out = out.drop_duplicates('month', keep='last')
    out['eur_per_usd'] = 1.0 / out['usd_per_eur']
    summary = {
        'series': 'EXR.M.USD.EUR.SP00.A',
        'rows': int(len(out)),
        'first_month': str(out.month.min().date()),
        'latest_month': str(out.month.max().date()),
    }
    return out, summary


def main() -> None:
    urls = {
        'sts_inpr_m': 'https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT/STS_INPR_M/1.0?compress=false&format=TSV&c[TIME_PERIOD]=ge:2000-01',
        'sts_inpp_m': 'https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT/STS_INPP_M/1.0?compress=false&format=TSV&c[TIME_PERIOD]=ge:2000-01',
        'ecb_eurusd': 'https://data-api.ecb.europa.eu/service/data/EXR/M.USD.EUR.SP00.A?startPeriod=1999-01&format=csvdata',
    }
    logs = {}
    eurostat_summaries = {}
    for dataset in ['sts_inpr_m', 'sts_inpp_m']:
        raw_path = OUT / f'{dataset}_raw.tsv'
        logs[dataset] = fetch_with_retry(urls[dataset], raw_path)
        if logs[dataset]['status'] == 'ok':
            panel, summary = parse_eurostat_tsv(raw_path, dataset)
            panel.to_csv(OUT / f'{dataset}_chemicals_basic_metals_2000_2026.csv.gz', index=False, compression='gzip')
            eurostat_summaries[dataset] = summary
            raw_path.unlink(missing_ok=True)
    ecb_path = OUT / 'ecb_eurusd_raw.csv'
    logs['ecb_eurusd'] = fetch_with_retry(urls['ecb_eurusd'], ecb_path, timeout=300)
    ecb_summary = None
    if logs['ecb_eurusd']['status'] == 'ok':
        ecb, ecb_summary = parse_ecb(ecb_path)
        ecb.to_csv(OUT / 'ecb_eur_usd_monthly_average_1999_2026.csv', index=False)
        ecb_path.unlink(missing_ok=True)
    result = {'downloads': logs, 'eurostat': eurostat_summaries, 'ecb': ecb_summary}
    (OUT / 'sts_ecb_summary.json').write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str), flush=True)
    failed = [k for k, v in logs.items() if v['status'] != 'ok']
    if failed:
        raise RuntimeError(f'Failed downloads: {failed}')


if __name__ == '__main__':
    main()
