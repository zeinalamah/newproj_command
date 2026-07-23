#!/usr/bin/env python3
from __future__ import annotations
import json, time
from pathlib import Path
import requests

OUT=Path('output/sts_inspect'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'
DATASETS=['sts_inpp_m','sts_inpr_m']

def fetch(url,params):
    for i in range(7):
        try:
            r=requests.get(url,params=params,timeout=300,headers={'User-Agent':'academic-research/1.0'}); r.raise_for_status(); return r.json()
        except Exception:
            if i==6: raise
            time.sleep(min(60,2**i))

def ordered_index(obj):
    idx=obj.get('category',{}).get('index',{})
    if isinstance(idx,list): return idx
    return [key for key,_ in sorted(idx.items(),key=lambda item:item[1])]

report={}
for dataset in DATASETS:
    payload=fetch(f'{BASE}/{dataset}',{'lang':'EN','geo':'DE','lastTimePeriod':2})
    dimensions={}
    for dim in payload.get('id',[]):
        obj=payload['dimension'][dim]
        codes=ordered_index(obj)
        labels=obj.get('category',{}).get('label',{})
        dimensions[dim]={'label':obj.get('label'),'codes':codes,'labels':{code:labels.get(code) for code in codes}}
    item={'title':payload.get('label'),'updated':payload.get('updated'),'id':payload.get('id'),'size':payload.get('size'),'dimensions':dimensions}
    report[dataset]=item
    (OUT/f'{dataset}_metadata.json').write_text(json.dumps(item,indent=2),encoding='utf-8')
(OUT/'sts_dimension_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
