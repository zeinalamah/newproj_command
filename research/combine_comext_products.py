#!/usr/bin/env python3
"""Combine product-specific historical Comext artifacts and construct treatments."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

IN=Path("research_output/product_artifacts"); OUT=Path("research_output/comext_combined"); OUT.mkdir(parents=True,exist_ok=True)
PRODUCTS={
"281410":("anhydrous_ammonia","primary_ammonia"),"281420":("aqueous_ammonia","near_product_control"),"310210":("urea","related_fertilizer"),"310230":("ammonium_nitrate","related_fertilizer"),"310240":("ammonium_nitrate_mixtures","related_fertilizer"),"310260":("calcium_ammonium_nitrate","related_fertilizer"),"310280":("urea_ammonium_nitrate_solution","related_fertilizer"),"760110":("unwrought_aluminium_not_alloyed","primary_aluminium"),"760120":("unwrought_aluminium_alloys","near_product_control"),"760200":("aluminium_waste_and_scrap","downstream_control"),"760310":("aluminium_powders_non_lamellar","downstream_control"),"760410":("aluminium_bars_rods_profiles_not_alloyed","downstream_control")}
HORMUZ={"AE","BH","IQ","IR","KW","QA","SA"}; EU27={"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
files=sorted(IN.rglob("comext_*_1988_2026.csv.gz")); summaries=sorted(IN.rglob("summary_*.json")); audits=sorted(IN.rglob("audit_*.csv"))
if not files:raise RuntimeError("No product files")
frames=[pd.read_csv(f,parse_dates=["month"],dtype={"reporter":str,"partner":str,"hs6":str}) for f in files]; d=pd.concat(frames,ignore_index=True)
d["hs6"]=d.hs6.str.zfill(6); meta=pd.DataFrame([{"hs6":k,"product_label":v[0],"product_role":v[1]} for k,v in PRODUCTS.items()]); d=d.merge(meta,on="hs6",how="left",validate="many_to_one")
d["hormuz_origin"]=d.partner.isin(HORMUZ).astype("int8"); d["oman_origin"]=d.partner.eq("OM").astype("int8"); d["intra_eu_origin"]=d.partner.isin(EU27).astype("int8"); d["post_2026"]=d.month.ge("2026-03-01").astype("int8")
d=d.groupby(["reporter","partner","month","hs6","product_label","product_role","hormuz_origin","oman_origin","intra_eu_origin","post_2026"],as_index=False).agg(trade_value_eur=("trade_value_eur","sum"),quantity_kg=("quantity_kg","sum"))
d["unit_value_eur_per_kg"]=np.where(d.quantity_kg.gt(0),d.trade_value_eur/d.quantity_kg,np.nan); d=d.sort_values(["hs6","reporter","partner","month"])
d.to_csv(OUT/"comext_selected_bilateral_1988_2026.csv.gz",index=False,compression="gzip")
# Reporter-product-month totals and network statistics.
def hhi(x):
 x=pd.to_numeric(x,errors="coerce").fillna(0).clip(lower=0); t=x.sum(); return float(((x/t)**2).sum()) if t>0 else np.nan
x=d.copy(); x["q"]=x.quantity_kg.fillna(0).clip(lower=0); x["v"]=x.trade_value_eur.fillna(0).clip(lower=0); x["hq"]=x.q*x.hormuz_origin; x["hv"]=x.v*x.hormuz_origin; x["oq"]=x.q*x.oman_origin; x["euq"]=x.q*x.intra_eu_origin; x["positive"]=x.q.gt(0)
keys=["reporter","month","hs6","product_label","product_role"]
monthly=x.groupby(keys,as_index=False).agg(total_import_quantity_kg=("q","sum"),total_import_value_eur=("v","sum"),hormuz_quantity_kg=("hq","sum"),hormuz_value_eur=("hv","sum"),oman_quantity_kg=("oq","sum"),intra_eu_quantity_kg=("euq","sum"),supplier_count=("positive","sum"))
conc=x.groupby(keys,as_index=False).agg(supplier_hhi_quantity=("q",hhi)); monthly=monthly.merge(conc,on=keys,how="left"); monthly["effective_suppliers"]=1/monthly.supplier_hhi_quantity; monthly["unit_value_eur_per_kg"]=np.where(monthly.total_import_quantity_kg.gt(0),monthly.total_import_value_eur/monthly.total_import_quantity_kg,np.nan); monthly["hormuz_share_quantity"]=np.where(monthly.total_import_quantity_kg.gt(0),monthly.hormuz_quantity_kg/monthly.total_import_quantity_kg,np.nan)
monthly.to_csv(OUT/"comext_importer_product_month_1988_2026.csv.gz",index=False,compression="gzip")
# 2019-2025 predetermined exposure.
pre=x[x.month.between("2019-01-01","2025-12-01")]; rows=[]
for (r,p,l,role),g in pre.groupby(["reporter","hs6","product_label","product_role"]):
 by=g.groupby("partner",as_index=False).q.sum(); total=by.q.sum(); shares=by.q/total if total>0 else pd.Series(dtype=float); hq=g.hq.sum()
 rows.append({"reporter":r,"hs6":p,"product_label":l,"product_role":role,"prewar_total_quantity_kg":total,"prewar_hormuz_quantity_kg":hq,"prewar_hormuz_share_quantity":hq/total if total>0 else np.nan,"prewar_supplier_count":int((by.q>0).sum()),"prewar_supplier_hhi_quantity":float((shares**2).sum()) if total>0 else np.nan,"prewar_effective_suppliers":float(1/(shares**2).sum()) if total>0 and (shares**2).sum()>0 else np.nan})
pd.DataFrame(rows).to_csv(OUT/"comext_prewar_exposure_2019_2025.csv",index=False)
coverage=d.groupby(["hs6","product_label","product_role"],as_index=False).agg(first_month=("month","min"),latest_month=("month","max"),reporters=("reporter","nunique"),partners=("partner","nunique"),rows=("month","size"),quantity_rows=("quantity_kg",lambda s:int(s.notna().sum())))
coverage.to_csv(OUT/"coverage.csv",index=False)
all_audits=pd.concat([pd.read_csv(f) for f in audits],ignore_index=True) if audits else pd.DataFrame(); all_audits.to_csv(OUT/"all_request_audits.csv",index=False)
summary={"files":len(files),"rows":len(d),"monthly_rows":len(monthly),"reporters":d.reporter.nunique(),"partners":d.partner.nunique(),"products":d.hs6.nunique(),"first_month":str(d.month.min().date()),"latest_month":str(d.month.max().date()),"terminal_failures":int((all_audits.status=="failed").sum()) if len(all_audits) else None,"product_summaries":[json.loads(f.read_text()) for f in summaries]}
(OUT/"build_summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps({k:v for k,v in summary.items() if k!="product_summaries"},indent=2),flush=True)
