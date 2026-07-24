#!/usr/bin/env python3
from __future__ import annotations
import json, time
from pathlib import Path
import pandas as pd
import requests

OUT=Path('research_output/sts_filtered'); OUT.mkdir(parents=True,exist_ok=True)
GEOS=['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','EL','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','EU27_2020','EA20']
BASE='https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/dataflow/ESTAT'

def get(url,path):
    last=None
    with requests.Session() as s:
        s.headers.update({'User-Agent':'academic-hormuz-energy-research/1.0'})
        for a in range(12):
            try:
                r=s.get(url,timeout=600)
                if r.status_code in {202,429,500,502,503,504}:
                    last=f'{r.status_code} {r.text[:300]}'; time.sleep(min(90,5*(a+1))); continue
                r.raise_for_status(); path.write_bytes(r.content); return {'status':'ok','bytes':path.stat().st_size,'attempts':a+1,'url':url}
            except Exception as e: last=repr(e); time.sleep(min(90,2**a))
    return {'status':'failed','error':last,'url':url}

def clean_csv(path):
    d=pd.read_csv(path,low_memory=False)
    d.columns=[str(c).strip().upper() for c in d.columns]
    t=next((c for c in ['TIME_PERIOD','TIME'] if c in d),None); v=next((c for c in ['OBS_VALUE','VALUE'] if c in d),None)
    if not t or not v: raise RuntimeError(f'Unexpected columns {d.columns.tolist()}')
    d['MONTH']=pd.to_datetime(d[t].astype(str).str.replace('M','-',regex=False),errors='coerce')
    d['VALUE']=pd.to_numeric(d[v],errors='coerce'); d=d[d.MONTH.notna()&d.VALUE.notna()].copy()
    return d

def main():
    geo=','.join(GEOS)
    common=f'format=csvdata&formatVersion=2.0&compress=false&c[FREQ]=M&c[UNIT]=I21&c[NACE_R2]=C20,C24&c[GEO]={geo}&c[TIME_PERIOD]=ge:2000-01'
    urls={
      'production':f'{BASE}/STS_INPR_M/1.0?{common}&c[S_ADJ]=SCA,CA,NSA',
      'producer_prices':f'{BASE}/STS_INPP_M/1.0?{common}',
      'ecb':'https://data-api.ecb.europa.eu/service/data/EXR/M.USD.EUR.SP00.A?startPeriod=1999-01&format=csvdata'}
    logs={}; summaries={}
    for name in ['production','producer_prices']:
        raw=OUT/f'{name}_raw.csv'; logs[name]=get(urls[name],raw)
        if logs[name]['status']=='ok':
            d=clean_csv(raw); d.to_csv(OUT/f'eurostat_{name}_C20_C24_2000_2026.csv.gz',index=False,compression='gzip')
            summaries[name]={'rows':len(d),'first_month':str(d.MONTH.min().date()),'latest_month':str(d.MONTH.max().date()),'columns':d.columns.tolist(),'geos':sorted(d.GEO.unique().tolist()) if 'GEO' in d else [],'nace':sorted(d.NACE_R2.unique().tolist()) if 'NACE_R2' in d else [],'indicators':sorted(d.INDIC_BT.unique().tolist()) if 'INDIC_BT' in d else [],'seasonal_adjustments':sorted(d.S_ADJ.unique().tolist()) if 'S_ADJ' in d else []}
            raw.unlink(missing_ok=True)
    raw=OUT/'ecb_raw.csv'; logs['ecb']=get(urls['ecb'],raw)
    if logs['ecb']['status']=='ok':
        d=pd.read_csv(raw); cols={c.upper():c for c in d.columns}; t=cols.get('TIME_PERIOD'); v=cols.get('OBS_VALUE')
        e=pd.DataFrame({'month':pd.to_datetime(d[t],errors='coerce'),'usd_per_eur':pd.to_numeric(d[v],errors='coerce')}).dropna().drop_duplicates('month').sort_values('month')
        e['eur_per_usd']=1/e.usd_per_eur; e.to_csv(OUT/'ecb_eur_usd_monthly_1999_2026.csv',index=False)
        summaries['ecb']={'rows':len(e),'first_month':str(e.month.min().date()),'latest_month':str(e.month.max().date())}; raw.unlink(missing_ok=True)
    result={'downloads':logs,'summaries':summaries}; (OUT/'summary.json').write_text(json.dumps(result,indent=2,default=str)); print(json.dumps(result,indent=2,default=str),flush=True)
    if any(v['status']!='ok' for v in logs.values()): raise RuntimeError('One or more downloads failed')
if __name__=='__main__': main()
