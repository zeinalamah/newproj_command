#!/usr/bin/env python3
from __future__ import annotations
import itertools,json,time
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from urllib.parse import urlencode

OUT=Path('research_output/comext_api_prehistory'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/ds-045409'
YEARS=[2021,2022,2023]; PRODUCTS=['281410','281420','760110','760120']
EU27={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}

def codes(payload,dim):
    idx=payload['dimension'][dim]['category'].get('index',{})
    return list(idx) if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]

def parse(payload):
    if 'id' not in payload:return pd.DataFrame()
    dims=list(payload['id']); shape=tuple(int(x) for x in payload['size']); cats={d:codes(payload,d) for d in dims}; vals=payload.get('value',{})
    it=enumerate(vals) if isinstance(vals,list) else ((int(k),v) for k,v in vals.items())
    rows=[]
    for flat,v in it:
        if v is None:continue
        coord=np.unravel_index(flat,shape,order='C'); row={d:cats[d][i] for d,i in zip(dims,coord)}; row['obs_value']=v; rows.append(row)
    return pd.DataFrame(rows)

def fetch(year,product):
    params=[('format','JSON'),('lang','en'),('freq','M'),('flow','1'),('product',product),('sinceTimePeriod',f'{year}-01'),('untilTimePeriod',f'{year}-12'),('indicators','VALUE_IN_EUROS'),('indicators','QUANTITY_IN_KG')]
    url=BASE+'?'+urlencode(params)
    last=None
    with requests.Session() as s:
        s.headers.update({'User-Agent':'academic-hormuz-energy-research/1.0'})
        for a in range(10):
            try:
                r=s.get(url,timeout=600)
                if r.status_code in {202,429,500,502,503,504}:last=f'{r.status_code} {r.text[:200]}';time.sleep(min(90,5*(a+1)));continue
                r.raise_for_status();payload=r.json();return payload,{'year':year,'product':product,'status':'ok','bytes':len(r.content),'attempts':a+1,'url':url}
            except Exception as e:last=repr(e);time.sleep(min(90,2**a))
    return {},{'year':year,'product':product,'status':'failed','error':last,'url':url}

def main():
    frames=[];logs=[]
    for year,product in itertools.product(YEARS,PRODUCTS):
        payload,log=fetch(year,product);logs.append(log);print(log,flush=True)
        if log['status']!='ok':continue
        raw=parse(payload)
        required={'reporter','partner','time','indicators'}
        if raw.empty or not required.issubset(raw.columns):log['parse_error']=f'columns {raw.columns.tolist()}';continue
        raw=raw[raw.reporter.isin(EU27)&raw.partner.str.fullmatch(r'[A-Z]{2}',na=False)].copy()
        if raw.empty:continue
        p=raw.pivot_table(index=['reporter','partner','time'],columns='indicators',values='obs_value',aggfunc='sum').reset_index().rename_axis(columns=None)
        p=p.rename(columns={'VALUE_IN_EUROS':'trade_value_eur','QUANTITY_IN_KG':'quantity_kg','time':'month'})
        for c in ['trade_value_eur','quantity_kg']:
            if c not in p:p[c]=np.nan
            p[c]=pd.to_numeric(p[c],errors='coerce')
        p['month']=pd.to_datetime(p.month.astype(str).str[:7]+'-01',errors='coerce');p['hs6']=product
        p=p[['reporter','partner','month','hs6','trade_value_eur','quantity_kg']];frames.append(p)
    pd.DataFrame(logs).to_csv(OUT/'request_audit.csv',index=False)
    if not frames:raise RuntimeError('No Comext API data')
    d=pd.concat(frames,ignore_index=True).groupby(['reporter','partner','month','hs6'],as_index=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'))
    d.to_csv(OUT/'comext_focal_bilateral_2021_2023.csv.gz',index=False,compression='gzip')
    cov=d.groupby(['hs6',d.month.dt.year.rename('year')],as_index=False).agg(rows=('reporter','size'),reporters=('reporter','nunique'),partners=('partner','nunique'),quantity_kg=('quantity_kg','sum'),trade_value_eur=('trade_value_eur','sum'))
    cov.to_csv(OUT/'coverage.csv',index=False)
    summary={'rows':len(d),'first_month':str(d.month.min().date()),'latest_month':str(d.month.max().date()),'reporters':d.reporter.nunique(),'partners':d.partner.nunique(),'products':d.hs6.nunique(),'failed_requests':[x for x in logs if x['status']!='ok']}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str));print(json.dumps(summary,indent=2,default=str),flush=True)
    if summary['failed_requests']:raise RuntimeError('Some requests failed')
if __name__=='__main__':main()
