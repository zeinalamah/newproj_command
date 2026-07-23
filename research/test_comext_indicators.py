#!/usr/bin/env python3
"""Probe the Comext indicator dimension and retain the raw response."""
from pathlib import Path
from urllib.parse import urlencode
import json, requests
OUT=Path('research_output/comext_indicator_probe'); OUT.mkdir(parents=True,exist_ok=True)
BASE='https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/ds-045409'
queries={
'all': [('format','JSON'),('lang','en'),('freq','M'),('flow','1'),('reporter','DE'),('product','281410'),('time','2024-01')],
'bulk_names': [('format','JSON'),('lang','en'),('freq','M'),('flow','1'),('reporter','DE'),('product','281410'),('time','2024-01'),('indicators','VALUE_IN_EUROS'),('indicators','QUANTITY_KG'),('indicators','QUANTITY_IN_KG'),('indicators','NET_MASS_IN_KG')],
}
report={}
for name,params in queries.items():
 url=f"{BASE}?{urlencode(params)}"; r=requests.get(url,timeout=180,headers={'User-Agent':'academic-hormuz-energy-paper/1.0'}); r.raise_for_status(); p=r.json(); (OUT/f'{name}.json').write_bytes(r.content)
 dim=p.get('dimension',{}).get('indicators',{}).get('category',{}); report[name]={'url':url,'status':r.status_code,'bytes':len(r.content),'ids':p.get('id'),'sizes':p.get('size'),'indicator_index':dim.get('index'),'indicator_labels':dim.get('label'),'observations':len(p.get('value',{})) if isinstance(p.get('value',{}),dict) else sum(x is not None for x in p.get('value',[]))}
(OUT/'report.json').write_text(json.dumps(report,indent=2,default=str)); print(json.dumps(report,indent=2,default=str))
