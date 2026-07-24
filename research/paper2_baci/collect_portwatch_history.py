#!/usr/bin/env python3
from __future__ import annotations
import json, math, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT=Path('research_output/portwatch'); OUT.mkdir(parents=True,exist_ok=True)
URL='https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query'
TARGET={'chokepoint1','chokepoint2','chokepoint4','chokepoint5','chokepoint6','chokepoint7','chokepoint8','chokepoint9'}
FIELDS=['date','year','month','day','portid','portname','n_container','n_dry_bulk','n_general_cargo','n_roro','n_tanker','n_cargo','n_total','capacity_container','capacity_dry_bulk','capacity_general_cargo','capacity_roro','capacity_tanker','capacity_cargo','capacity','ObjectId']

def get_json(params):
    last=None
    with requests.Session() as s:
        s.headers.update({'User-Agent':'academic-hormuz-energy-research/1.0'})
        for attempt in range(10):
            try:
                r=s.get(URL,params=params,timeout=180); r.raise_for_status(); payload=r.json()
                if 'error' in payload: raise RuntimeError(payload['error'])
                return payload
            except Exception as e:
                last=repr(e); time.sleep(min(60,2**attempt))
    raise RuntimeError(last)

def main():
    rows=[]; offset=0; page=1000; audits=[]
    where="portid IN ("+','.join(f"'{x}'" for x in sorted(TARGET))+") AND date >= DATE '2019-01-01'"
    while True:
        params={'where':where,'outFields':','.join(FIELDS),'returnGeometry':'false','orderByFields':'ObjectId','resultOffset':offset,'resultRecordCount':page,'f':'json'}
        payload=get_json(params); feats=payload.get('features',[])
        if not feats: break
        attrs=[x['attributes'] for x in feats]; rows.extend(attrs)
        audits.append({'offset':offset,'rows':len(attrs),'exceededTransferLimit':payload.get('exceededTransferLimit',False)})
        print(audits[-1],flush=True)
        offset += len(attrs)
        if len(attrs)<page and not payload.get('exceededTransferLimit',False): break
    d=pd.DataFrame(rows)
    if d.empty: raise RuntimeError('No PortWatch rows')
    d['date']=pd.to_datetime(d['date'],errors='coerce')
    d=d[d.portid.isin(TARGET)&d.date.notna()].copy()
    d=d.sort_values(['portid','date']).drop_duplicates(['portid','date'],keep='last')
    d['post']=d.date.ge('2026-03-01').astype('int8'); d['hormuz']=d.portid.eq('chokepoint6').astype('int8')
    for src,dst in [('n_total','log_total'),('n_tanker','log_tanker'),('capacity','log_capacity'),('capacity_tanker','log_tanker_capacity')]:
        d[dst]=np.log1p(pd.to_numeric(d[src],errors='coerce').clip(lower=0))
    d.to_csv(OUT/'portwatch_chokepoints_daily_2019_2026.csv.gz',index=False,compression='gzip')
    coverage=d.groupby(['portid','portname'],as_index=False).agg(first_date=('date','min'),latest_date=('date','max'),observations=('date','size'),missing_total=('n_total',lambda x:int(x.isna().sum())))
    coverage.to_csv(OUT/'portwatch_coverage.csv',index=False)
    pd.DataFrame(audits).to_csv(OUT/'portwatch_pagination_audit.csv',index=False)
    summary={'source':URL,'rows':int(len(d)),'chokepoints':int(d.portid.nunique()),'first_date':str(d.date.min().date()),'latest_date':str(d.date.max().date()),'hormuz_rows':int((d.hormuz==1).sum()),'pages':len(audits),'coverage':coverage.astype(str).to_dict('records')}
    (OUT/'portwatch_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__': main()
