#!/usr/bin/env python3
"""Retain CN8 detail for main ammonia and unwrought-aluminium products.

The official Eurostat full bulk archives are processed sequentially for 2024,
2025, and January-May 2026. CN8 detail is needed to distinguish true price
changes from shifts among product subcategories hidden by HS6 aggregation.
"""
from __future__ import annotations
import json, shutil, time
from pathlib import Path
import pandas as pd, py7zr, requests
BASE='https://ec.europa.eu/eurostat/api/dissemination/files'; OUT=Path('research_output/comext_cn8'); RAW=OUT/'raw'; TMP=OUT/'tmp'; [p.mkdir(parents=True,exist_ok=True) for p in [OUT,RAW,TMP]]
EU={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
PREFIXES={'281410':'anhydrous_ammonia','281420':'aqueous_ammonia','760110':'unwrought_aluminium_not_alloyed','760120':'unwrought_aluminium_alloys'}
ARCHIVES=['202452','202552','202601','202602','202603','202604','202605']
USE=['REPORTER','PARTNER','PRODUCT_NC','FLOW','PERIOD','VALUE_EUR','QUANTITY_KG','SUP_QUANTITY','SUP_UNIT']
def download(s,remote,dest):
 last=None
 for attempt in range(6):
  try:
   with s.get(BASE,params={'file':remote},stream=True,timeout=900) as r:
    r.raise_for_status()
    with dest.open('wb') as f:
     for chunk in r.iter_content(2*1024*1024):
      if chunk:f.write(chunk)
   return {'remote':remote,'status':'ok','bytes':dest.stat().st_size}
  except Exception as e:
   last=repr(e); dest.unlink(missing_ok=True)
   if attempt==5:return {'remote':remote,'status':'failed','error':last}
   time.sleep(min(90,2**attempt))
def read_file(path):
 frames=[]; raw=selected=0
 header=pd.read_csv(path,nrows=0,dtype=str).columns.tolist(); use=[c for c in USE if c in header]
 required={'REPORTER','PARTNER','PRODUCT_NC','FLOW','PERIOD','VALUE_EUR','QUANTITY_KG'}
 if not required.issubset(use):raise RuntimeError(f'Missing required columns in {path}: {header}')
 for chunk in pd.read_csv(path,usecols=use,dtype=str,chunksize=500000,low_memory=False):
  raw+=len(chunk); prod=chunk.PRODUCT_NC.fillna('').str.zfill(8); hs6=prod.str[:6]; partner=chunk.PARTNER.fillna('')
  mask=chunk.REPORTER.isin(EU)&chunk.FLOW.eq('1')&hs6.isin(PREFIXES)&partner.str.fullmatch(r'[A-Z]{2}')
  x=chunk.loc[mask].copy(); selected+=len(x)
  if len(x):
   x['cn8']=prod[mask].values; x['hs6']=hs6[mask].values; frames.append(x)
 return (pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()),raw,selected
def main():
 logs=[]; frames=[]
 with requests.Session() as s:
  s.headers.update({'User-Agent':'academic-hormuz-energy-paper/1.0'})
  for code in ARCHIVES:
   remote=f'comext/COMEXT_DATA/PRODUCTS/full_v2_{code}.7z'; archive=RAW/f'full_v2_{code}.7z'; log=download(s,remote,archive); log['archive_code']=code; print(log,flush=True)
   if log['status']!='ok':logs.append(log); continue
   ex=TMP/code; shutil.rmtree(ex,ignore_errors=True); ex.mkdir(parents=True)
   with py7zr.SevenZipFile(archive) as z:z.extractall(ex); log['members']=z.getnames()
   ar=sel=0
   for f in ex.rglob('*'):
    if not f.is_file():continue
    try:
     d,r,k=read_file(f); ar+=r; sel+=k
     if len(d):frames.append(d)
    except Exception as e:log.setdefault('parse_errors',[]).append({'file':str(f),'error':repr(e)})
   log['raw_rows']=ar; log['selected_rows']=sel; logs.append(log); shutil.rmtree(ex,ignore_errors=True); archive.unlink(missing_ok=True)
 pd.DataFrame(logs).to_csv(OUT/'download_processing_log.csv',index=False)
 if not frames:raise RuntimeError('No selected observations')
 d=pd.concat(frames,ignore_index=True); d['month']=pd.to_datetime(d.PERIOD.astype(str).str.extract(r'(\d{6})',expand=False)+'01',format='%Y%m%d',errors='coerce'); d=d[d.month.between('2024-01-01','2026-05-01')].copy(); d['trade_value_eur']=pd.to_numeric(d.VALUE_EUR,errors='coerce'); d['quantity_kg']=pd.to_numeric(d.QUANTITY_KG,errors='coerce'); d['supplementary_quantity']=pd.to_numeric(d.SUP_QUANTITY,errors='coerce') if 'SUP_QUANTITY' in d else pd.NA; d['supplementary_unit']=d.SUP_UNIT if 'SUP_UNIT' in d else pd.NA
 keys=['REPORTER','PARTNER','month','cn8','hs6','supplementary_unit']; out=d.groupby(keys,dropna=False,as_index=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'),supplementary_quantity=('supplementary_quantity','sum'),raw_rows=('hs6','size')).rename(columns={'REPORTER':'reporter','PARTNER':'partner'}); out['product_label']=out.hs6.map(PREFIXES); out=out.sort_values(['hs6','cn8','reporter','partner','month']); out.to_csv(OUT/'comext_main_products_cn8_2024_2026.csv.gz',index=False,compression='gzip')
 coverage=out.groupby(['hs6','product_label','cn8','supplementary_unit'],dropna=False,as_index=False).agg(rows=('month','size'),reporters=('reporter','nunique'),partners=('partner','nunique'),first_month=('month','min'),latest_month=('month','max'),quantity_kg=('quantity_kg','sum'),value_eur=('trade_value_eur','sum')); coverage.to_csv(OUT/'cn8_coverage.csv',index=False)
 summary={'rows':len(out),'reporters':out.reporter.nunique(),'partners':out.partner.nunique(),'hs6_products':out.hs6.nunique(),'cn8_products':out.cn8.nunique(),'first_month':str(out.month.min().date()),'latest_month':str(out.month.max().date()),'failed_archives':[x['archive_code'] for x in logs if x['status']!='ok']}; (OUT/'summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
