#!/usr/bin/env python3
"""Collect a broad monthly manufacturing control pool from Eurostat STS.

Coverage is 2000-2026 for total/domestic/non-domestic producer prices and
industrial production. All NACE two-digit manufacturing sectors C10-C33 plus
fertilizer and aluminium detail are retained. No key or subscription is needed.
"""
from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any,Iterable
from urllib.parse import urlencode
import numpy as np,pandas as pd,requests
BASE='https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'; OUT=Path('research_output/sts_control_pool'); OUT.mkdir(parents=True,exist_ok=True)
EU={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','EL','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
DATASETS={'sts_inpp_m':'ppi_total','sts_inppd_m':'ppi_domestic','sts_inppnd_m':'ppi_non_domestic','sts_inpr_m':'production'}
SECTORS=[f'C{x}' for x in range(10,34)]+['C201','C2015','C244','C2442']
def ordered(p,d):
 idx=p['dimension'][d]['category'].get('index',{}); return list(idx) if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]
def parse(p:dict[str,Any])->pd.DataFrame:
 if 'id' not in p:return pd.DataFrame()
 dims=list(p['id']); shape=tuple(map(int,p['size'])); cats={d:ordered(p,d) for d in dims}; raw=p.get('value',{}); it:Iterable[tuple[int,Any]]=enumerate(raw) if isinstance(raw,list) else ((int(k),v) for k,v in raw.items()); rows=[]
 for flat,v in it:
  if v is None:continue
  c=np.unravel_index(flat,shape,order='C'); rows.append({**{d:cats[d][i] for d,i in zip(dims,c)},'value':v})
 return pd.DataFrame(rows)
def fetch(s,ds,nace):
 params=[('format','JSON'),('lang','en'),('freq','M'),('nace_r2',nace),('sinceTimePeriod','2000-01'),('untilTimePeriod','2026-06')]; url=f'{BASE}/{ds}?{urlencode(params)}'; last=None
 for attempt in range(6):
  try:
   t=time.time(); r=s.get(url,timeout=600); r.raise_for_status(); p=r.json(); d=parse(p); return d,{'dataset':ds,'nace_requested':nace,'status':'ok','observations':len(d),'bytes':len(r.content),'seconds':time.time()-t,'id':p.get('id'),'size':p.get('size')}
  except Exception as e:last=repr(e); time.sleep(min(60,2**attempt))
 return pd.DataFrame(),{'dataset':ds,'nace_requested':nace,'status':'failed','error':last}
def main():
 frames=[]; audit=[]
 with requests.Session() as s:
  s.headers.update({'User-Agent':'academic-hormuz-energy-paper/1.0'})
  for ds,outcome in DATASETS.items():
   for nace in SECTORS:
    d,a=fetch(s,ds,nace); audit.append(a); print(ds,nace,a['status'],a.get('observations'),flush=True)
    if len(d):d['dataset']=ds; d['outcome']=outcome; d['nace_requested']=nace; frames.append(d)
 pd.DataFrame(audit).to_csv(OUT/'request_audit.csv',index=False)
 if not frames:raise RuntimeError('No observations')
 d=pd.concat(frames,ignore_index=True); d=d[d.geo.isin(EU)].copy(); d['geo']=d.geo.replace({'EL':'GR'}); d['month']=pd.to_datetime(d.time.astype(str)+'-01',errors='coerce'); d['value']=pd.to_numeric(d.value,errors='coerce'); keep=[c for c in ['dataset','outcome','nace_requested','nace_r2','geo','unit','s_adj','month','value'] if c in d]; d=d[keep].drop_duplicates().sort_values(['outcome','nace_requested','geo','month']); d.to_csv(OUT/'eurostat_sts_manufacturing_control_pool_2000_2026.csv.gz',index=False,compression='gzip')
 series=[c for c in ['dataset','outcome','nace_requested','nace_r2','geo','unit','s_adj'] if c in d]; cov=d.groupby(series,dropna=False,as_index=False).agg(n=('value','count'),first=('month','min'),last=('month','max')); cov.to_csv(OUT/'series_coverage.csv',index=False)
 summary={'rows':len(d),'countries':d.geo.nunique(),'requested_sectors':len(SECTORS),'published_nace_codes':sorted(d.nace_r2.dropna().astype(str).unique()) if 'nace_r2' in d else [],'first_month':str(d.month.min().date()),'latest_month':str(d.month.max().date()),'failed_requests':[a for a in audit if a['status']!='ok']}; (OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str),flush=True)
if __name__=='__main__':main()
