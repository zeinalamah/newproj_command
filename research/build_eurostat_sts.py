#!/usr/bin/env python3
"""Collect monthly Eurostat producer-price and industrial-production outcomes."""
from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any,Iterable
from urllib.parse import urlencode
import numpy as np
import pandas as pd
import requests
BASE="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"; OUT=Path("research_output/eurostat_sts"); OUT.mkdir(parents=True,exist_ok=True)
EU={"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","EL","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
DATASETS={"sts_inpp_m":"producer_price_total","sts_inppd_m":"producer_price_domestic","sts_inppnd_m":"producer_price_non_domestic","sts_inpr_m":"industrial_production"}
NACE={"C20":"chemicals","C201":"basic_chemicals_fertilizers_plastics","C2015":"fertilisers_nitrogen_compounds","C24":"basic_metals","C244":"nonferrous_metals","C2442":"aluminium_production","C25":"fabricated_metal_control"}
def ordered(p,d):
 idx=p["dimension"][d]["category"].get("index",{}); return list(idx) if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]
def parse(p:dict[str,Any])->pd.DataFrame:
 if "id" not in p:return pd.DataFrame()
 dims=list(p["id"]); shape=tuple(map(int,p["size"])); cats={d:ordered(p,d) for d in dims}; raw=p.get("value",{}); it:Iterable[tuple[int,Any]]=enumerate(raw) if isinstance(raw,list) else ((int(k),v) for k,v in raw.items()); rows=[]
 for flat,v in it:
  if v is None:continue
  coord=np.unravel_index(flat,shape,order="C"); rows.append({**{d:cats[d][i] for d,i in zip(dims,coord)},"value":v})
 return pd.DataFrame(rows)
def fetch(s,dataset,nace):
 params=[("format","JSON"),("lang","en"),("freq","M"),("nace_r2",nace),("sinceTimePeriod","2000-01"),("untilTimePeriod","2026-06")]; url=f"{BASE}/{dataset}?{urlencode(params)}"; begun=time.time(); last=None
 for attempt in range(6):
  try:
   r=s.get(url,timeout=600); r.raise_for_status(); p=r.json(); d=parse(p); return d,{"dataset":dataset,"nace":nace,"status":"ok","observations":len(d),"bytes":len(r.content),"seconds":time.time()-begun,"dimensions":p.get("id"),"size":p.get("size"),"url":url}
  except Exception as exc:
   last=repr(exc); time.sleep(min(60,2**attempt))
 return pd.DataFrame(),{"dataset":dataset,"nace":nace,"status":"failed","error":last,"url":url}
def main():
 frames=[]; audits=[]
 with requests.Session() as s:
  s.headers.update({"User-Agent":"academic-hormuz-energy-paper/1.0"})
  for ds,outcome in DATASETS.items():
   for nace,label in NACE.items():
    d,a=fetch(s,ds,nace); audits.append(a); print(ds,nace,a["status"],a.get("observations"),flush=True)
    if len(d):d["dataset"]=ds; d["outcome"]=outcome; d["industry_label"]=label; d["nace_requested"]=nace; frames.append(d)
 pd.DataFrame(audits).to_csv(OUT/"request_audit.csv",index=False)
 if not frames:raise RuntimeError("No STS observations")
 d=pd.concat(frames,ignore_index=True); d=d[d.geo.isin(EU)].copy(); d["geo"]=d.geo.replace({"EL":"GR"}); d["month"]=pd.to_datetime(d.time.astype(str)+"-01",errors="coerce"); d["value"]=pd.to_numeric(d.value,errors="coerce"); keep=[c for c in ["dataset","outcome","industry_label","nace_requested","freq","s_adj","unit","nace_r2","geo","month","value"] if c in d]; d=d[keep].drop_duplicates(); d.to_csv(OUT/"eurostat_sts_all_series_2000_2026.csv.gz",index=False,compression="gzip")
 # Series-level coverage. Preferred-series selection is performed in the analysis package.
 series=[c for c in ["dataset","outcome","industry_label","nace_requested","nace_r2","geo","unit","s_adj"] if c in d]; cov=d.groupby(series,dropna=False,as_index=False).agg(observations=("value","count"),first_month=("month","min"),latest_month=("month","max")); cov.to_csv(OUT/"series_coverage.csv",index=False)
 detail=cov.groupby(["outcome","nace_requested","industry_label"],as_index=False).agg(countries=("geo","nunique"),series=("geo","size"),observations=("observations","sum"),first_month=("first_month","min"),latest_month=("latest_month","max"),countries_with_postwar=("latest_month",lambda s:int(pd.to_datetime(s).ge("2026-03-01").sum()))); detail.to_csv(OUT/"industry_coverage_summary.csv",index=False)
 summary={"rows":len(d),"countries":d.geo.nunique(),"first_month":str(d.month.min().date()),"latest_month":str(d.month.max().date()),"datasets":DATASETS,"nace":NACE,"failed_requests":[a for a in audits if a["status"]!="ok"]}; (OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str),flush=True)
if __name__=="__main__":main()
