#!/usr/bin/env python3
"""Combine annual broad Comext panels and construct screening/analysis tables."""
from pathlib import Path
import json, sqlite3
import numpy as np
import pandas as pd

ROOT=Path('research_output/energy_intensive');ROOT.mkdir(parents=True,exist_ok=True)
files=sorted(ROOT.glob('year_*/comext_energy_intensive_*.parquet'))
if not files:raise RuntimeError('No annual parquet files')
d=pd.concat([pd.read_parquet(p) for p in files],ignore_index=True)
d=d.sort_values(['month','reporter','partner','cn8']).drop_duplicates(['reporter','partner','month','cn8'],keep='last')
STRICT={'BH','IQ','IR','KW','QA'};BROAD=STRICT|{'AE','SA'};CALL=BROAD|{'OM'}
d['strict_hormuz']=d.partner.isin(STRICT).astype('int8');d['broad_gulf']=d.partner.isin(BROAD).astype('int8');d['call_region']=d.partner.isin(CALL).astype('int8')
d.to_parquet(ROOT/'energy_intensive_bilateral_2015_2026.parquet',index=False);d.to_csv(ROOT/'energy_intensive_bilateral_2015_2026.csv.gz',index=False,compression='gzip')

x=d.copy()
for nm in ['strict_hormuz','broad_gulf','call_region']:
 x[nm+'_quantity_kg']=x.quantity_kg*x[nm];x[nm+'_value_eur']=x.trade_value_eur*x[nm]
keys=['reporter','hs6','month','product_family','energy_intensity_score','nace_r2']
m=x.groupby(keys,as_index=False).agg(total_value_eur=('trade_value_eur','sum'),total_quantity_kg=('quantity_kg','sum'),supplier_count=('partner','nunique'),strict_hormuz_quantity_kg=('strict_hormuz_quantity_kg','sum'),broad_gulf_quantity_kg=('broad_gulf_quantity_kg','sum'),call_region_quantity_kg=('call_region_quantity_kg','sum'))
def hhi(g):
 q=g.groupby('partner').quantity_kg.sum().clip(lower=0);t=q.sum();return ((q/t)**2).sum() if t>0 else np.nan
hh=x.groupby(keys).apply(hhi,include_groups=False).rename('supplier_hhi_quantity').reset_index();m=m.merge(hh,on=keys,how='left');m['unit_value_eur_kg']=np.where(m.total_quantity_kg.gt(0),m.total_value_eur/m.total_quantity_kg,np.nan)
m.to_parquet(ROOT/'importer_product_month_2015_2026.parquet',index=False);m.to_csv(ROOT/'importer_product_month_2015_2026.csv.gz',index=False,compression='gzip')

pre=m[m.month.between('2024-01-01','2025-12-01')]
e=pre.groupby(['reporter','hs6','product_family','energy_intensity_score','nace_r2'],as_index=False).agg(prewar_value_eur=('total_value_eur','sum'),prewar_quantity_kg=('total_quantity_kg','sum'),prewar_supplier_count=('supplier_count','mean'),prewar_hhi=('supplier_hhi_quantity','mean'),strict_hormuz_quantity_kg=('strict_hormuz_quantity_kg','sum'),broad_gulf_quantity_kg=('broad_gulf_quantity_kg','sum'),call_region_quantity_kg=('call_region_quantity_kg','sum'))
for nm in ['strict_hormuz','broad_gulf','call_region']:e[nm+'_share']=e[nm+'_quantity_kg']/e.prewar_quantity_kg.replace(0,np.nan)
e.to_csv(ROOT/'prewar_exposure_2024_2025.csv',index=False)

base=m[m.month.isin(pd.to_datetime(['2025-03-01','2025-04-01']))];post=m[m.month.isin(pd.to_datetime(['2026-03-01','2026-04-01']))]
def col(z,p):
 return z.groupby(['hs6','product_family','energy_intensity_score','nace_r2'],as_index=False).agg(**{p+'_value_eur':('total_value_eur','sum'),p+'_quantity_kg':('total_quantity_kg','sum'),p+'_affected_quantity_kg':('call_region_quantity_kg','sum'),p+'_suppliers':('supplier_count','mean'),p+'_hhi':('supplier_hhi_quantity','mean')})
s=col(base,'base').merge(col(post,'post'),on=['hs6','product_family','energy_intensity_score','nace_r2'],how='outer')
pe=e.groupby(['hs6','product_family'],as_index=False).agg(prewar_value_eur=('prewar_value_eur','sum'),prewar_quantity_kg=('prewar_quantity_kg','sum'),prewar_affected_quantity_kg=('call_region_quantity_kg','sum'),exposed_importers=('call_region_share',lambda q:int((q>=.01).sum())),median_hhi=('prewar_hhi','median'))
s=s.merge(pe,on=['hs6','product_family'],how='left');s['prewar_affected_share']=s.prewar_affected_quantity_kg/s.prewar_quantity_kg.replace(0,np.nan);s['affected_log_change']=np.log1p(s.post_affected_quantity_kg/1000)-np.log1p(s.base_affected_quantity_kg/1000);s['total_log_change']=np.log1p(s.post_quantity_kg/1000)-np.log1p(s.base_quantity_kg/1000);s['base_uv']=s.base_value_eur/s.base_quantity_kg.replace(0,np.nan);s['post_uv']=s.post_value_eur/s.post_quantity_kg.replace(0,np.nan);s['unit_value_log_change']=np.log(s.post_uv)-np.log(s.base_uv);s['lost_affected_kg']=(s.base_affected_quantity_kg-s.post_affected_quantity_kg).clip(lower=0);s['nongulf_replacement_kg']=((s.post_quantity_kg-s.post_affected_quantity_kg)-(s.base_quantity_kg-s.base_affected_quantity_kg)).clip(lower=0);s['replacement_ratio']=s.nongulf_replacement_kg/s.lost_affected_kg.replace(0,np.nan)
# Penalize tiny/noisy markets and require meaningful prewar exposure/size.
s['eligible_size']=(s.prewar_value_eur>=10_000_000)&(s.prewar_quantity_kg>=1_000_000);s['candidate_score']=s.eligible_size.astype(int)*(100*s.prewar_affected_share.fillna(0)*np.maximum(0,-s.affected_log_change.fillna(0))*np.log1p(s.prewar_value_eur.fillna(0)/1e6)+5*np.maximum(0,s.unit_value_log_change.fillna(0))+2*np.log1p(s.exposed_importers.fillna(0)))
s=s.sort_values('candidate_score',ascending=False);s.to_csv(ROOT/'product_candidate_ranking.csv',index=False)
f=s.groupby('product_family',as_index=False).agg(products=('hs6','nunique'),eligible_products=('eligible_size','sum'),prewar_value_eur=('prewar_value_eur','sum'),prewar_quantity_kg=('prewar_quantity_kg','sum'),prewar_affected_quantity_kg=('prewar_affected_quantity_kg','sum'),lost_affected_kg=('lost_affected_kg','sum'),nongulf_replacement_kg=('nongulf_replacement_kg','sum'),median_uv_change=('unit_value_log_change','median'),max_candidate_score=('candidate_score','max'));f['prewar_affected_share']=f.prewar_affected_quantity_kg/f.prewar_quantity_kg.replace(0,np.nan);f['replacement_ratio']=f.nongulf_replacement_kg/f.lost_affected_kg.replace(0,np.nan);f=f.sort_values('max_candidate_score',ascending=False);f.to_csv(ROOT/'family_candidate_ranking.csv',index=False)

# Historical placebo changes using the same March-April window for every year.
rows=[]
for year in range(2016,2026):
 b=m[m.month.isin(pd.to_datetime([f'{year-1}-03-01',f'{year-1}-04-01']))];p=m[m.month.isin(pd.to_datetime([f'{year}-03-01',f'{year}-04-01']))]
 bb=col(b,'base');pp=col(p,'post');z=bb.merge(pp,on=['hs6','product_family','energy_intensity_score','nace_r2'],how='outer');z['year']=year;z['affected_log_change']=np.log1p(z.post_affected_quantity_kg/1000)-np.log1p(z.base_affected_quantity_kg/1000);z['total_log_change']=np.log1p(z.post_quantity_kg/1000)-np.log1p(z.base_quantity_kg/1000);z['base_uv']=z.base_value_eur/z.base_quantity_kg.replace(0,np.nan);z['post_uv']=z.post_value_eur/z.post_quantity_kg.replace(0,np.nan);z['unit_value_log_change']=np.log(z.post_uv)-np.log(z.base_uv);rows.append(z)
placebo=pd.concat(rows,ignore_index=True);placebo.to_csv(ROOT/'historical_mar_apr_placebos_2016_2025.csv.gz',index=False,compression='gzip')

with sqlite3.connect(ROOT/'energy_intensive_products_2015_2026.sqlite') as con:
 d.to_sql('bilateral',con,if_exists='replace',index=False,chunksize=100000);m.to_sql('importer_product_month',con,if_exists='replace',index=False,chunksize=100000);e.to_sql('prewar_exposure',con,if_exists='replace',index=False);s.to_sql('product_ranking',con,if_exists='replace',index=False);f.to_sql('family_ranking',con,if_exists='replace',index=False);placebo.to_sql('historical_placebos',con,if_exists='replace',index=False)
summary={'annual_files':len(files),'rows':len(d),'start':str(d.month.min()),'end':str(d.month.max()),'reporters':d.reporter.nunique(),'partners':d.partner.nunique(),'cn8':d.cn8.nunique(),'hs6':d.hs6.nunique(),'families':d.product_family.nunique(),'top_products':s.head(25)[['hs6','product_family','candidate_score']].to_dict('records')};(ROOT/'build_summary.json').write_text(json.dumps(summary,indent=2,default=str));print(json.dumps(summary,indent=2),flush=True)
