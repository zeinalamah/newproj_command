#!/usr/bin/env python3
import json, time
from pathlib import Path
import pandas as pd
import requests

OUT=Path('output/comtrade_mirror_test'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://comtradeapi.un.org/public/v1/preview/C/M/HS'
queries=[
 {'name':'affected_group','params':{'period':'202603','cmdCode':'760110','flowCode':'M','partnerCode':'48,364,414,634,682,784','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
 {'name':'uae_only','params':{'period':'202603','cmdCode':'760110','flowCode':'M','partnerCode':'784','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
 {'name':'bahrain_only','params':{'period':'202603','cmdCode':'760110','flowCode':'M','partnerCode':'48','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
 {'name':'qatar_ammonia','params':{'period':'202603','cmdCode':'281410','flowCode':'M','partnerCode':'634','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
]
records=[]
with requests.Session() as s:
 s.headers.update({'User-Agent':'academic-hormuz-energy-paper/1.0'})
 for q in queries:
  rec={'name':q['name'],'params':q['params']}
  for attempt in range(8):
   try:
    r=s.get(BASE,params=q['params'],timeout=180)
    if r.status_code==429:
     time.sleep(2+attempt); continue
    rec['status_code']=r.status_code; rec['url']=r.url; r.raise_for_status(); payload=r.json()
    rec['count']=payload.get('count'); rec['rows']=len(payload.get('data',[])); rec['error_field']=payload.get('error'); rec['sample']=payload.get('data',[])[:10]
    pd.DataFrame(payload.get('data',[])).to_csv(OUT/f"{q['name']}.csv",index=False)
    break
   except Exception as e:
    rec['error']=repr(e)
    if attempt==7: break
    time.sleep(min(30,2**attempt))
  records.append(rec); print(json.dumps(rec,indent=2,default=str)); time.sleep(1.5)
(OUT/'results.json').write_text(json.dumps(records,indent=2,default=str))
