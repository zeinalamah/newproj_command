#!/usr/bin/env python3
"""Collect one HS6 product from Eurostat Comext, monthly 1988-2026.

A query covers all reporters and partners for one product, indicator, and short
time block. Blocks that exceed Eurostat's cell limit are recursively divided.
Only EU27 import reporters and bilateral two-letter partners are retained.
Comext reports net mass through QUANTITY_IN_100KG; this script converts it to kg.
"""
from __future__ import annotations
import argparse, json, random, time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE="https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/ds-045409"
EU27={"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
INDICATORS={"VALUE_IN_EUROS":"trade_value_eur","QUANTITY_IN_100KG":"quantity_100kg"}

def ordered(payload:dict[str,Any], dim:str)->list[str]:
    idx=payload["dimension"][dim]["category"].get("index",{})
    return list(idx) if isinstance(idx,list) else [k for k,_ in sorted(idx.items(),key=lambda kv:kv[1])]

def jsonstat(payload:dict[str,Any])->pd.DataFrame:
    if "id" not in payload or "size" not in payload:return pd.DataFrame()
    dims=list(payload["id"]); shape=tuple(map(int,payload["size"])); cats={d:ordered(payload,d) for d in dims}; values=payload.get("value",{})
    iterator:Iterable[tuple[int,Any]]=enumerate(values) if isinstance(values,list) else ((int(k),v) for k,v in values.items())
    rows=[]
    for flat,value in iterator:
        if value is None:continue
        coord=np.unravel_index(flat,shape,order="C")
        rows.append({**{d:cats[d][i] for d,i in zip(dims,coord)},"obs_value":value})
    return pd.DataFrame(rows)

def make_session()->requests.Session:
    s=requests.Session(); retry=Retry(total=6,connect=6,read=6,backoff_factor=1,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]),respect_retry_after_header=True)
    s.mount("https://",HTTPAdapter(max_retries=retry)); s.headers.update({"User-Agent":"academic-hormuz-energy-paper/1.0"}); return s

def split_range(start:str,end:str):
    a,b=pd.Period(start,freq="M"),pd.Period(end,freq="M")
    if a>=b:return None
    n=b.ordinal-a.ordinal+1; le=a+(n//2-1); return str(le),str(le+1)

def request(s,product,indicator,start,end):
    params=[("format","JSON"),("lang","en"),("freq","M"),("flow","1"),("product",product),("indicators",indicator),("sinceTimePeriod",start),("untilTimePeriod",end)]
    url=f"{BASE}?{urlencode(params)}"; begun=time.time(); r=s.get(url,timeout=900); r.raise_for_status(); payload=r.json()
    if payload.get("error"):raise RuntimeError(payload["error"])
    df=jsonstat(payload)
    returned=set(df["indicators"].astype(str)) if len(df) and "indicators" in df else set()
    if len(df) and indicator not in returned:
        raise RuntimeError(f"Requested indicator {indicator} but API returned {sorted(returned)}")
    return df,{"product":product,"indicator":indicator,"start":start,"end":end,"status":"ok","seconds":time.time()-begun,"bytes":len(r.content),"observations":len(df),"size":payload.get("size"),"returned_indicators":"|".join(sorted(returned))}

def recursive(s,product,indicator,start,end,depth=0):
    try:
        df,a=request(s,product,indicator,start,end); time.sleep(.1+random.random()*.15); return ([df] if len(df) else []),[a]
    except Exception as exc:
        split=split_range(start,end)
        if split is None or depth>=8:return [],[{"product":product,"indicator":indicator,"start":start,"end":end,"status":"failed","error":repr(exc),"depth":depth}]
        left_end,right_start=split; f1,a1=recursive(s,product,indicator,start,left_end,depth+1); f2,a2=recursive(s,product,indicator,right_start,end,depth+1)
        return f1+f2,[{"product":product,"indicator":indicator,"start":start,"end":end,"status":"split","error":repr(exc),"depth":depth}]+a1+a2

def blocks(start,end,years=2):
    a,b=pd.Period(start,freq="M"),pd.Period(end,freq="M"); out=[]
    while a<=b:
        e=min(b,a+years*12-1); out.append((str(a),str(e))); a=e+1
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--product",required=True); ap.add_argument("--start",default="1988-01"); ap.add_argument("--end",default="2026-06"); ap.add_argument("--out",type=Path,required=True); args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    frames=[]; audits=[]
    with make_session() as s:
        for indicator in INDICATORS:
            for start,end in blocks(args.start,args.end):
                f,a=recursive(s,args.product,indicator,start,end); frames.extend(f); audits.extend(a); print(args.product,indicator,start,end,"frames",len(f),flush=True)
    pd.DataFrame(audits).to_csv(args.out/f"audit_{args.product}.csv",index=False)
    failures=[a for a in audits if a.get("status")=="failed"]
    if not frames:raise RuntimeError("No observations")
    raw=pd.concat(frames,ignore_index=True)
    raw=raw[raw["reporter"].isin(EU27)&raw["partner"].astype(str).str.fullmatch(r"[A-Z]{2}")].copy(); raw["month"]=pd.to_datetime(raw["time"].astype(str)+"-01",errors="coerce")
    long=raw.groupby(["reporter","partner","month","indicators"],as_index=False).obs_value.sum(); wide=long.pivot_table(index=["reporter","partner","month"],columns="indicators",values="obs_value",aggfunc="sum").reset_index().rename_axis(columns=None)
    for source,target in INDICATORS.items():wide[target]=pd.to_numeric(wide[source],errors="coerce") if source in wide else np.nan
    wide["quantity_kg"]=wide["quantity_100kg"]*100.0
    wide["hs6"]=args.product; wide.loc[wide.trade_value_eur.lt(0),"trade_value_eur"]=np.nan; wide.loc[wide.quantity_kg.lt(0),"quantity_kg"]=np.nan; wide["unit_value_eur_per_kg"]=np.where(wide.quantity_kg.gt(0),wide.trade_value_eur/wide.quantity_kg,np.nan)
    wide[["reporter","partner","month","hs6","trade_value_eur","quantity_kg","unit_value_eur_per_kg"]].sort_values(["reporter","partner","month"]).to_csv(args.out/f"comext_{args.product}_1988_2026.csv.gz",index=False,compression="gzip")
    summary={"product":args.product,"rows":len(wide),"reporters":wide.reporter.nunique(),"partners":wide.partner.nunique(),"first_month":str(wide.month.min().date()),"latest_month":str(wide.month.max().date()),"positive_quantity_rows":int(wide.quantity_kg.fillna(0).gt(0).sum()),"successful_calls":sum(a.get("status")=="ok" for a in audits),"split_calls":sum(a.get("status")=="split" for a in audits),"terminal_failures":failures}
    (args.out/f"summary_{args.product}.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2),flush=True)
    if failures:raise SystemExit(2)
if __name__=="__main__":main()
