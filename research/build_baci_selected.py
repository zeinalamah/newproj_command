#!/usr/bin/env python3
"""Filter CEPII BACI HS92 (1995-2024) to selected embodied-energy products.

The 2.36GB official ZIP is streamed to /tmp and read member-by-member without
extraction. Output provides global bilateral trade, exporter concentration, and
alternative-supplier capacity proxies. BACI is free under Etalab 2.0.
"""
from __future__ import annotations
import json,re,time,zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests
URL="https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_HS92_V202601.zip"; ZIP=Path("/tmp/BACI_HS92_V202601.zip"); OUT=Path("research_output/baci_selected"); OUT.mkdir(parents=True,exist_ok=True)
PRODUCTS={"281410":("anhydrous_ammonia","primary"),"281420":("aqueous_ammonia","control"),"310210":("urea","related"),"310230":("ammonium_nitrate","related"),"310240":("ammonium_nitrate_mixtures","related"),"310260":("calcium_ammonium_nitrate","related"),"310280":("urea_ammonium_nitrate_solution","related"),"760110":("unwrought_aluminium_not_alloyed","primary"),"760120":("unwrought_aluminium_alloys","control"),"760200":("aluminium_waste_scrap","downstream"),"760310":("aluminium_powders","downstream"),"760410":("aluminium_bars_profiles","downstream")}; TARGET=set(PRODUCTS); HORMUZ={"ARE","BHR","IRQ","IRN","KWT","QAT","SAU"}
def download():
 for attempt in range(6):
  try:
   with requests.get(URL,stream=True,timeout=1800,headers={"User-Agent":"academic-hormuz-energy-paper/1.0"}) as r:
    r.raise_for_status()
    with ZIP.open("wb") as f:
     for chunk in r.iter_content(4*1024*1024):
      if chunk:f.write(chunk)
   return ZIP.stat().st_size
  except Exception:
   ZIP.unlink(missing_ok=True)
   if attempt==5:raise
   time.sleep(min(120,2**attempt))
def main():
 size=download(); selected=[]; audits=[]
 with zipfile.ZipFile(ZIP) as z:
  names=z.namelist(); country=next(n for n in names if re.search(r"country.*codes.*\.csv$",n,re.I)); product=next((n for n in names if re.search(r"product.*codes.*HS92.*\.csv$",n,re.I)),None)
  with z.open(country) as f:cc=pd.read_csv(f,dtype=str,encoding_errors="replace")
  cc.to_csv(OUT/"country_codes.csv",index=False)
  if product:
   with z.open(product) as f:pd.read_csv(f,dtype=str,encoding_errors="replace").to_csv(OUT/"product_codes_hs92.csv",index=False)
  annual=sorted(n for n in names if re.search(r"BACI_HS92_Y\d{4}_V202601\.csv$",n))
  for idx,name in enumerate(annual,1):
   raw=keep=0; parts=[]
   with z.open(name) as f:
    for chunk in pd.read_csv(f,dtype={"k":str},chunksize=750000,low_memory=False):
     raw+=len(chunk); chunk["k"]=chunk.k.astype(str).str.zfill(6); x=chunk[chunk.k.isin(TARGET)].copy(); keep+=len(x)
     if len(x):parts.append(x)
   if parts:selected.append(pd.concat(parts,ignore_index=True))
   year=int(re.search(r"_Y(\d{4})_",name).group(1)); audits.append({"year":year,"member":name,"raw_rows":raw,"selected_rows":keep}); print(year,keep,flush=True)
 ZIP.unlink(missing_ok=True)
 d=pd.concat(selected,ignore_index=True); d["k"]=d.k.astype(str).str.zfill(6); d["v"]=pd.to_numeric(d.v,errors="coerce"); d["q"]=pd.to_numeric(d.q,errors="coerce"); d["i"]=d.i.astype(str); d["j"]=d.j.astype(str)
 cc.columns=[str(c).strip().lower() for c in cc.columns]; code=next(c for c in ["country_code","i","iso_num","iso3n"] if c in cc); iso=next(c for c in ["iso_3digit_alpha","iso3","iso3c"] if c in cc); name=next(c for c in ["country_name_full","country_name","name"] if c in cc); m=cc[[code,iso,name]].drop_duplicates(); m[code]=m[code].astype(str)
 d=d.merge(m.rename(columns={code:"i",iso:"exporter_iso3",name:"exporter_name"}),on="i",how="left",validate="many_to_one").merge(m.rename(columns={code:"j",iso:"importer_iso3",name:"importer_name"}),on="j",how="left",validate="many_to_one")
 meta=pd.DataFrame([{"hs6":k,"product_label":v[0],"role":v[1]} for k,v in PRODUCTS.items()]); d=d.rename(columns={"t":"year","k":"hs6","v":"value_thousand_usd","q":"quantity_tonnes"}).merge(meta,on="hs6",how="left"); d["hormuz_exporter"]=d.exporter_iso3.isin(HORMUZ).astype("int8"); d["oman_exporter"]=d.exporter_iso3.eq("OMN").astype("int8"); d["unit_value_usd_per_tonne"]=np.where(d.quantity_tonnes.gt(0),1000*d.value_thousand_usd/d.quantity_tonnes,np.nan)
 cols=["year","exporter_iso3","exporter_name","importer_iso3","importer_name","hs6","product_label","role","value_thousand_usd","quantity_tonnes","unit_value_usd_per_tonne","hormuz_exporter","oman_exporter"]; d=d[cols].sort_values(["year","hs6","exporter_iso3","importer_iso3"]); d.to_csv(OUT/"baci_selected_bilateral_1995_2024.csv.gz",index=False,compression="gzip")
 ex=d.groupby(["year","hs6","product_label","role","exporter_iso3","exporter_name","hormuz_exporter","oman_exporter"],as_index=False).agg(export_value_thousand_usd=("value_thousand_usd","sum"),export_quantity_tonnes=("quantity_tonnes","sum"),importer_count=("importer_iso3","nunique")); total=ex.groupby(["year","hs6"],as_index=False).agg(global_quantity=("export_quantity_tonnes","sum"),global_value=("export_value_thousand_usd","sum")); ex=ex.merge(total,on=["year","hs6"]); ex["share_quantity"]=ex.export_quantity_tonnes/ex.global_quantity; ex.to_csv(OUT/"baci_global_exporters_1995_2024.csv.gz",index=False,compression="gzip")
 structure=ex.groupby(["year","hs6","product_label","role"],as_index=False).agg(global_quantity=("global_quantity","first"),exporter_count=("exporter_iso3","nunique"),exporter_hhi=("share_quantity",lambda s:float((s**2).sum())),top_exporter_share=("share_quantity","max"),hormuz_share=("share_quantity",lambda s:float(s[ex.loc[s.index,"hormuz_exporter"].eq(1)].sum()))); structure["effective_exporters"]=1/structure.exporter_hhi; structure.to_csv(OUT/"baci_global_market_structure_1995_2024.csv",index=False)
 recent=ex[ex.year.between(2015,2024)]; cap=recent.groupby(["hs6","product_label","role","exporter_iso3","exporter_name","hormuz_exporter","oman_exporter"],as_index=False).agg(avg_exports_2015_2024=("export_quantity_tonnes","mean"),max_exports_2015_2024=("export_quantity_tonnes","max"),latest_exports=("export_quantity_tonnes","last"),latest_year=("year","max"),years=("year","nunique")); cap["spare_capacity_proxy"]=np.maximum(0,cap.max_exports_2015_2024-cap.latest_exports); cap.to_csv(OUT/"baci_alternative_supplier_capacity.csv",index=False)
 pd.DataFrame(audits).to_csv(OUT/"member_audit.csv",index=False); summary={"url":URL,"download_bytes":size,"rows":len(d),"years":[int(d.year.min()),int(d.year.max())],"products":d.hs6.nunique(),"exporters":d.exporter_iso3.nunique(),"importers":d.importer_iso3.nunique(),"license":"Etalab 2.0"}; (OUT/"summary.json").write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2),flush=True)
if __name__=="__main__":main()
