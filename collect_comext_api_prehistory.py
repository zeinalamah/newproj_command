#!/usr/bin/env python3
"""Collect focal EU imports from Comext for 2021–2023.

DS-045409 reports physical quantity in 100-kilogram units. The collector
requests QUANTITY_IN_100KG and converts it to kilograms explicitly.
"""
from __future__ import annotations
import itertools,json,time
from pathlib import Path
from urllib.parse import urlencode
import numpy as np
import pandas as pd
import requests

OUT=Path('research_output'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/ds-045409'
YEARS=[2021,2022,2023]; PRODUCTS=['281410','281420','760110','760120']
EU27={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
MEASURES=['VALUE_IN_EUROS','QUANTITY_IN_100KG']

def dim_codes(payload,dim):
    idx=payload['dimension'][dim]['category'].get('index',{})
    return list(idx) if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]

def parse_jsonstat(payload):
    if 'id' not in payload:return pd.DataFrame()
    dims=list(payload['id']);shape=tuple(map(int,payload['size']));cats={d:dim_codes(payload,d) for d in dims};vals=payload.get('value',{})
    iterator=enumerate(vals) if isinstance(vals,list) else ((int(k),v) for k,v in vals.items())
    rows=[]
    for flat,value in iterator:
        if value is None:continue
        coord=np.unravel_index(flat,shape,order='C');row={d:cats[d][i] for d,i in zip(dims,coord)};row['obs_value']=value;rows.append(row)
    return pd.DataFrame(rows)

def fetch(year,product):
    params=[('format','JSON'),('lang','en'),('freq','M'),('flow','1'),('product',product),('sinceTimePeriod',f'{year}-01'),('untilTimePeriod',f'{year}-12')]
    params.extend(('indicators',measure) for measure in MEASURES)
    url=BASE+'?'+urlencode(params);last=None
    with requests.Session() as session:
        session.headers.update({'User-Agent':'academic-hormuz-energy-research/1.0'})
        for attempt in range(10):
            try:
                response=session.get(url,timeout=600)
                if response.status_code in {202,429,500,502,503,504}:
                    last=f'HTTP {response.status_code}: {response.text[:200]}';time.sleep(min(90,5*(attempt+1)));continue
                response.raise_for_status();payload=response.json()
                indicator_codes=dim_codes(payload,'indicators') if 'indicators' in payload.get('dimension',{}) else []
                return payload,{'year':year,'product':product,'status':'ok','bytes':len(response.content),'attempts':attempt+1,'indicator_codes':'|'.join(indicator_codes),'url':url}
            except Exception as exc:last=repr(exc);time.sleep(min(90,2**attempt))
    return {},{'year':year,'product':product,'status':'failed','error':last,'url':url}

def main():
    frames=[];logs=[]
    for year,product in itertools.product(YEARS,PRODUCTS):
        payload,log=fetch(year,product);logs.append(log);print(log,flush=True)
        if log['status']!='ok':continue
        raw=parse_jsonstat(payload);needed={'reporter','partner','time','indicators'}
        if raw.empty or not needed.issubset(raw.columns):log['parse_error']=f'columns={raw.columns.tolist()}';continue
        raw=raw[raw.reporter.isin(EU27)&raw.partner.str.fullmatch(r'[A-Z]{2}',na=False)].copy()
        if raw.empty:continue
        pivot=raw.pivot_table(index=['reporter','partner','time'],columns='indicators',values='obs_value',aggfunc='sum').reset_index().rename_axis(columns=None)
        pivot=pivot.rename(columns={'VALUE_IN_EUROS':'trade_value_eur','QUANTITY_IN_100KG':'quantity_100kg','time':'month'})
        for col in ['trade_value_eur','quantity_100kg']:
            if col not in pivot:pivot[col]=np.nan
            pivot[col]=pd.to_numeric(pivot[col],errors='coerce')
        pivot['quantity_kg']=pivot['quantity_100kg']*100.0
        pivot['month']=pd.to_datetime(pivot.month.astype(str).str[:7]+'-01',errors='coerce');pivot['hs6']=product
        frames.append(pivot[['reporter','partner','month','hs6','trade_value_eur','quantity_kg']])
    pd.DataFrame(logs).to_csv(OUT/'request_audit.csv',index=False)
    if not frames:raise RuntimeError('No Comext observations collected')
    data=pd.concat(frames,ignore_index=True).groupby(['reporter','partner','month','hs6'],as_index=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'))
    data.to_csv(OUT/'comext_focal_bilateral_2021_2023.csv.gz',index=False,compression='gzip')
    coverage=data.groupby(['hs6',data.month.dt.year.rename('year')],as_index=False).agg(rows=('reporter','size'),reporters=('reporter','nunique'),partners=('partner','nunique'),positive_quantity_rows=('quantity_kg',lambda s:int(s.gt(0).sum())),quantity_kg=('quantity_kg','sum'),trade_value_eur=('trade_value_eur','sum'))
    coverage.to_csv(OUT/'coverage.csv',index=False)
    failed=[x for x in logs if x['status']!='ok' or x.get('parse_error')]
    quantity_valid=bool(data['quantity_kg'].gt(0).any())
    summary={'rows':len(data),'first_month':str(data.month.min().date()),'latest_month':str(data.month.max().date()),'reporters':data.reporter.nunique(),'partners':data.partner.nunique(),'products':data.hs6.nunique(),'positive_quantity_rows':int(data.quantity_kg.gt(0).sum()),'quantity_total_kg':float(data.quantity_kg.sum()),'failed_requests':failed,'quantity_measure':'QUANTITY_IN_100KG multiplied by 100'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str));print(json.dumps(summary,indent=2,default=str),flush=True)
    if failed or not quantity_valid:raise RuntimeError('Some requests failed or quantity validation failed')
if __name__=='__main__':main()
