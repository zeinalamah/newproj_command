#!/usr/bin/env python3
from __future__ import annotations
import json, time
from pathlib import Path
import pandas as pd
import requests

OUT=Path('output/comtrade_test'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://comtradeapi.un.org/public/v1/preview/C/M/HS'
queries=[
  {'name':'omit_reporter','params':{'period':'202603','cmdCode':'760110','flowCode':'X','partnerCode':'0','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
  {'name':'reporter_all','params':{'period':'202603','reporterCode':'all','cmdCode':'760110','flowCode':'X','partnerCode':'0','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
  {'name':'reporter_zero','params':{'period':'202603','reporterCode':'0','cmdCode':'760110','flowCode':'X','partnerCode':'0','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
  {'name':'qatar','params':{'period':'202603','reporterCode':'634','cmdCode':'760110','flowCode':'X','partnerCode':'0','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
  {'name':'uae','params':{'period':'202603','reporterCode':'784','cmdCode':'760110','flowCode':'X','partnerCode':'0','partner2Code':'0','maxRecords':'500','includeDesc':'true','breakdownMode':'classic'}},
]
records=[]
with requests.Session() as s:
 s.headers.update({'User-Agent':'academic-hormuz-energy-paper/1.0'})
 for q in queries:
  rec={'name':q['name'],'params':q['params']}
  try:
   r=s.get(BASE,params=q['params'],timeout=180); rec['status_code']=r.status_code; rec['url']=r.url; rec['text_prefix']=r.text[:500]
   r.raise_for_status(); payload=r.json(); rec['keys']=list(payload); rec['validation']=payload.get('validation'); rec['count']=payload.get('count'); rec['data_rows']=len(payload.get('data',[])); rec['sample']=payload.get('data',[])[:5]
   pd.DataFrame(payload.get('data',[])).to_csv(OUT/f"{q['name']}.csv",index=False)
  except Exception as e: rec['error']=repr(e)
  records.append(rec); print(json.dumps(rec,indent=2,default=str)); time.sleep(1.2)
(OUT/'test_results.json').write_text(json.dumps(records,indent=2,default=str))
