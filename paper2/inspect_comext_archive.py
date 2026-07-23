#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, io, json, re, time
from pathlib import Path
import pandas as pd
import py7zr
import requests

BASE='https://ec.europa.eu/eurostat/api/dissemination/files'
DIR='comext/COMEXT_DATA/PRODUCTS'

def get(session, params, stream=False, timeout=600):
    for i in range(7):
        try:
            r=session.get(BASE,params=params,stream=stream,timeout=timeout); r.raise_for_status(); return r
        except requests.RequestException:
            if i==6: raise
            time.sleep(min(60,2**i))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    with requests.Session() as s:
        s.headers.update({'User-Agent':'academic-research/1.0'})
        r=get(s,{'format':'csv','dir':DIR,'hierarchy':'false','sizeFormat':'NONE','dateFormat':'ISO','sort':'name'},timeout=180)
        li=pd.read_csv(io.StringIO(r.text),dtype=str); li.to_csv(a.out/'listing.csv',index=False)
        names=li.NAME.dropna().tolist()
        annual=[n for n in names if re.search(fr'{a.year}52',n) and n.startswith('full_v2_')]
        monthly=[n for n in names if re.search(fr'{a.year}(?:0[1-9]|1[0-2])',n) and n.startswith('full_v2_')]
        name=(annual or sorted(monthly))[0]
        arc=a.out/name
        r=get(s,{'file':f'{DIR}/{name}'},stream=True,timeout=900)
        with arc.open('wb') as f:
            for ch in r.iter_content(1024*1024):
                if ch: f.write(ch)
    ext=a.out/'ext'; ext.mkdir(exist_ok=True)
    with py7zr.SevenZipFile(arc,'r') as z: contained=z.getnames(); z.extractall(ext)
    dat=[p for p in ext.rglob('*') if p.is_file()][0]
    sample=pd.read_csv(dat,dtype=str,nrows=500000,low_memory=False)
    sample.head(500).to_csv(a.out/'raw_sample_500.csv',index=False)
    cols={c.upper():c for c in sample.columns}
    prod=sample[cols['PRODUCT_NC']].fillna('').astype(str).str.replace(r'\.0$','',regex=True).str.zfill(8)
    rep=sample[cols['REPORTER']].fillna('').astype(str).str.strip()
    par=sample[cols['PARTNER']].fillna('').astype(str).str.strip()
    flow=sample[cols['FLOW']].fillna('').astype(str).str.strip()
    target=sample.loc[prod.str[:2].isin(['28','31','76'])].copy()
    target.head(500).to_csv(a.out/'target_sample_500.csv',index=False)
    report={'archive':name,'archive_bytes':arc.stat().st_size,'contained':contained,'columns':sample.columns.tolist(),'sample_rows':len(sample),'reporter_top':rep.value_counts().head(50).to_dict(),'partner_top':par.value_counts().head(50).to_dict(),'flow_top':flow.value_counts().head(20).to_dict(),'product_prefix_top':prod.str[:2].value_counts().head(50).to_dict(),'target_rows':int(len(target)),'target_reporter_top':target[cols['REPORTER']].value_counts().head(30).to_dict(),'target_partner_top':target[cols['PARTNER']].value_counts().head(30).to_dict(),'target_flow_top':target[cols['FLOW']].value_counts().head(20).to_dict(),'target_product_top':target[cols['PRODUCT_NC']].value_counts().head(30).to_dict()}
    (a.out/'inspect.json').write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))
if __name__=='__main__': main()
