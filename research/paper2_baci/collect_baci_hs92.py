#!/usr/bin/env python3
from __future__ import annotations
import json, re, zipfile
from pathlib import Path
import pandas as pd

ARCHIVE=Path('research_output/baci/BACI_HS92_V202601.zip')
OUT=Path('research_output/baci'); OUT.mkdir(parents=True,exist_ok=True)
FOCAL={'281410','281420','760110','760120'}
EU27={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
ROUTE={'BH','IQ','IR','KW','QA','AE','SA'}; STRICT={'BH','IQ','IR','KW','QA'}

def country_map(z):
    names=[n for n in z.namelist() if 'country_codes' in n.lower() and n.lower().endswith('.csv')]
    if not names: raise RuntimeError('country_codes metadata missing')
    with z.open(names[0]) as f: c=pd.read_csv(f,dtype=str)
    lower={x.lower():x for x in c.columns}
    code=next((v for k,v in lower.items() if k=='country_code' or ('country' in k and 'code' in k)),None)
    iso2=next((v for k,v in lower.items() if 'iso_2' in k or 'iso2' in k),None)
    if not code or not iso2: raise RuntimeError(f'country metadata columns {c.columns.tolist()}')
    m=c[[code,iso2]].rename(columns={code:'code',iso2:'iso2'}).dropna().drop_duplicates('code')
    m['code']=m.code.astype(str); m['iso2']=m.iso2.str.upper()
    return m

def hhi(s):
    s=pd.to_numeric(s,errors='coerce').fillna(0).clip(lower=0); total=s.sum()
    return float(((s/total)**2).sum()) if total>0 else None

def main():
    if not ARCHIVE.exists(): raise FileNotFoundError(ARCHIVE)
    frames=[]; audit=[]
    with zipfile.ZipFile(ARCHIVE) as z:
        m=country_map(z); eu=set(m.loc[m.iso2.isin(EU27),'code'])
        annual=sorted(n for n in z.namelist() if re.search(r'BACI_HS92_Y(19|20)\d{2}_V202601\.csv$',n))
        if not annual: raise RuntimeError('annual trade files missing')
        for name in annual:
            year=int(re.search(r'_Y(\d{4})_',name).group(1)); selected=[]; raw=0
            with z.open(name) as f:
                for ch in pd.read_csv(f,dtype={'k':str,'i':str,'j':str},chunksize=800000):
                    raw+=len(ch); ch['k']=ch.k.astype(str).str.zfill(6)
                    keep=ch.k.isin(FOCAL)&ch.j.astype(str).isin(eu)
                    if keep.any(): selected.append(ch.loc[keep,['t','k','i','j','v','q']])
            if selected:
                x=pd.concat(selected,ignore_index=True)
                x=x.merge(m.rename(columns={'code':'i','iso2':'exporter'}),on='i',how='left')
                x=x.merge(m.rename(columns={'code':'j','iso2':'importer'}),on='j',how='left')
                x=x.rename(columns={'t':'year','k':'hs6','v':'value_thousand_usd','q':'quantity_tonnes'})
                x['year']=pd.to_numeric(x.year,errors='coerce').astype('Int64')
                x['value_thousand_usd']=pd.to_numeric(x.value_thousand_usd,errors='coerce')
                x['quantity_tonnes']=pd.to_numeric(x.quantity_tonnes,errors='coerce')
                x=x[['year','hs6','exporter','importer','value_thousand_usd','quantity_tonnes']]
                frames.append(x); kept=len(x)
            else: kept=0
            audit.append({'year':year,'raw_rows':raw,'selected_rows':kept}); print(audit[-1],flush=True)
    panel=pd.concat(frames,ignore_index=True)
    panel['route']=panel.exporter.isin(ROUTE).astype('int8'); panel['strict']=panel.exporter.isin(STRICT).astype('int8')
    panel.to_csv(OUT/'baci_focal_eu_imports_1995_2024.csv.gz',index=False,compression='gzip')
    pd.DataFrame(audit).to_csv(OUT/'baci_collection_audit.csv',index=False)
    panel['value_usd']=panel.value_thousand_usd*1000
    panel['route_value_usd']=panel.value_usd*panel.route; panel['strict_value_usd']=panel.value_usd*panel.strict
    stats=panel.groupby(['year','importer','hs6'],as_index=False).agg(total_value_usd=('value_usd','sum'),total_quantity_tonnes=('quantity_tonnes','sum'),route_value_usd=('route_value_usd','sum'),strict_value_usd=('strict_value_usd','sum'),supplier_count=('exporter','nunique'))
    hh=panel.groupby(['year','importer','hs6']).value_usd.apply(hhi).rename('supplier_hhi_value').reset_index(); stats=stats.merge(hh,on=['year','importer','hs6'])
    stats['route_share_value']=stats.route_value_usd/stats.total_value_usd.where(stats.total_value_usd>0)
    stats['strict_share_value']=stats.strict_value_usd/stats.total_value_usd.where(stats.total_value_usd>0)
    stats.to_csv(OUT/'baci_importer_product_network_1995_2024.csv.gz',index=False,compression='gzip')
    # Annual product-origin aggregates support historical shock and placebo diagnostics.
    agg=panel.groupby(['year','hs6','exporter','route','strict'],as_index=False).agg(value_thousand_usd=('value_thousand_usd','sum'),quantity_tonnes=('quantity_tonnes','sum'),eu_importers=('importer','nunique'))
    agg.to_csv(OUT/'baci_product_origin_annual_1995_2024.csv.gz',index=False,compression='gzip')
    summary={'source':'CEPII BACI HS92 V202601','rows':int(len(panel)),'start_year':int(panel.year.min()),'end_year':int(panel.year.max()),'products':int(panel.hs6.nunique()),'exporters':int(panel.exporter.nunique()),'importers':int(panel.importer.nunique()),'network_rows':int(len(stats)),'origin_annual_rows':int(len(agg))}
    (OUT/'baci_summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__': main()
