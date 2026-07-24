#!/usr/bin/env python3
"""Collect one year of EU27 monthly imports for a broad energy-intensive universe."""
from __future__ import annotations
import argparse, io, json, re, shutil, time
from pathlib import Path
import numpy as np
import pandas as pd
import py7zr, requests

FILES_ENDPOINT='https://ec.europa.eu/eurostat/api/dissemination/files'
PRODUCT_DIR='comext/COMEXT_DATA/PRODUCTS'
EU27={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
USECOLS=['REPORTER','PARTNER','PRODUCT_NC','FLOW','PERIOD','VALUE_EUR','QUANTITY_KG']
RULES=[
 ('ammonia',5,'C2015',{'281410','281420'},set()),
 ('nitrogen_fertilizer',5,'C2015',set(),{'3102'}),
 ('phosphate_potash_compound_fertilizer',4,'C2015',set(),{'3103','3104','3105'}),
 ('sulphur_phosphate_inputs',4,'C2013',{'250300','251010','251020','280200','280700','280920'},set()),
 ('industrial_gases',5,'C2011',{'280410','281121'},set()),
 ('chlor_alkali',5,'C2013',{'280110','281511','281512','281520','283620'},set()),
 ('carbon_black',5,'C2013',{'280300'},set()),
 ('silicon_and_carbides',5,'C2013',{'280461','280469','284910','284920'},set()),
 ('basic_petrochemicals',5,'C2014',{'290121','290122','290124','290220','290230','290241','290242','290243','290250','290511','290531','290532','291411','291521'},set()),
 ('primary_polymers',4,'C2016',set(),{'3901','3902','3903','3904'}),
 ('synthetic_rubber',4,'C2017',set(),{'4002'}),
 ('cement_clinker_lime_gypsum',5,'C235',set(),{'2520','2521','2522','2523'}),
 ('flat_glass_and_fibres',5,'C231',set(),{'7002','7003','7004','7005','7006','7019'}),
 ('refractory_and_structural_ceramics',4,'C232',set(),{'6902','6904','6907','6910'}),
 ('pulp',5,'C171',set(),{'4701','4702','4703','4704','4705','4706'}),
 ('paper_and_paperboard',4,'C171',set(),{'4801','4802','4803','4804','4805'}),
 ('pig_iron_dri_ferroalloys',5,'C241',set(),{'7201','7202','7203'}),
 ('semifinished_and_flat_steel',5,'C241',set(),{'7206','7207','7208','7209','7210','7211','7212'}),
 ('steel_bars_wire_sections',4,'C243',set(),{'7213','7214','7215','7216','7217'}),
 ('primary_copper',5,'C2444',set(),{'7402','7403'}),('copper_semis',4,'C2444',set(),{'7407','7408','7409'}),
 ('primary_nickel',5,'C2445',set(),{'7502'}),('nickel_semis',4,'C2445',set(),{'7505','7506'}),
 ('primary_aluminium',5,'C2442',set(),{'7601'}),('aluminium_semis',4,'C2442',set(),{'7604','7605','7606','7607'}),
 ('primary_lead',5,'C2443',set(),{'7801'}),('lead_semis',4,'C2443',set(),{'7804'}),
 ('primary_zinc',5,'C2443',set(),{'7901'}),('zinc_semis',4,'C2443',set(),{'7904','7905'}),
 ('primary_tin',5,'C2445',set(),{'8001'}),('tin_semis',4,'C2445',set(),{'8003'}),
 ('magnesium',5,'C2445',set(),{'8104'}),('titanium',5,'C2445',set(),{'8108'}),('manganese',5,'C2445',set(),{'8111'}),
]

def classify(code):
 code=str(code).zfill(8); h6,h4=code[:6],code[:4]
 for fam,score,nace,exact,prefixes in RULES:
  if h6 in exact or h4 in prefixes:return fam,score,nace
 return None

def get(session,params,stream=False,timeout=900):
 last=None
 for attempt in range(7):
  try:
   r=session.get(FILES_ENDPOINT,params=params,stream=stream,timeout=timeout);r.raise_for_status();return r
  except requests.RequestException as exc:
   last=exc
   if attempt==6:raise
   time.sleep(min(90,2**attempt))
 raise RuntimeError(last)

def listing(session):
 r=get(session,{'format':'csv','dir':PRODUCT_DIR,'hierarchy':'false','sizeFormat':'NONE','dateFormat':'ISO','sort':'name'},timeout=180)
 f=pd.read_csv(io.StringIO(r.text),dtype=str);f.columns=[str(c).strip().upper() for c in f.columns];return f

def archive_names(frame,year):
 pat=re.compile(fr'^full_v2_{year}(0[1-9]|1[0-2])\.7z$')
 return sorted(n for n in frame['NAME'].dropna().tolist() if pat.match(n))

def download(session,name,dest):
 r=get(session,{'file':f'{PRODUCT_DIR}/{name}'},stream=True)
 with dest.open('wb') as h:
  for chunk in r.iter_content(1024*1024):
   if chunk:h.write(chunk)
 return dest.stat().st_size

def process(path,archive,year):
 frames=[];raw_rows=selected_rows=0;months={f'{m:02d}' for m in range(1,13)}
 for ch in pd.read_csv(path,usecols=lambda c:c in USECOLS,dtype=str,chunksize=500000,low_memory=False):
  raw_rows+=len(ch);reporter=ch.REPORTER.fillna('').str.strip().str.upper();partner=ch.PARTNER.fillna('').str.strip().str.upper();product=ch.PRODUCT_NC.fillna('').str.replace(r'\.0$','',regex=True).str.zfill(8);flow=ch.FLOW.fillna('').str.strip().str.upper();period=ch.PERIOD.fillna('').str.extract(r'(\d{6})',expand=False)
  import_flow=flow.isin({'1','01','I','IMP','IMPORT'})|flow.str.startswith('1.')
  meta=product.map(classify);mask=reporter.isin(EU27)&partner.str.fullmatch(r'[A-Z]{2}')&meta.notna()&import_flow&period.str[:4].eq(str(year))&period.str[4:6].isin(months)
  if not mask.any():continue
  vals=meta[mask].tolist();z=pd.DataFrame({'reporter':reporter[mask].values,'partner':partner[mask].values,'month':pd.to_datetime(period[mask].values.astype(str)+'01',format='%Y%m%d',errors='coerce'),'cn8':product[mask].values,'trade_value_eur':pd.to_numeric(ch.loc[mask,'VALUE_EUR'],errors='coerce').values,'quantity_kg':pd.to_numeric(ch.loc[mask,'QUANTITY_KG'],errors='coerce').values})
  z['hs6']=z.cn8.str[:6];z['product_family']=[v[0] for v in vals];z['energy_intensity_score']=[v[1] for v in vals];z['nace_r2']=[v[2] for v in vals];z=z.dropna(subset=['month']);selected_rows+=len(z);frames.append(z)
 if not frames:return pd.DataFrame(),{'archive':archive,'raw_rows':raw_rows,'selected_rows':0,'aggregated_rows':0}
 d=pd.concat(frames,ignore_index=True);d['negative_value_flag']=d.trade_value_eur.lt(0).astype('int8');d['negative_quantity_flag']=d.quantity_kg.lt(0).astype('int8');d.loc[d.trade_value_eur.lt(0),'trade_value_eur']=np.nan;d.loc[d.quantity_kg.lt(0),'quantity_kg']=np.nan
 d=d.groupby(['reporter','partner','month','cn8','hs6','product_family','energy_intensity_score','nace_r2'],as_index=False,dropna=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'),raw_rows=('hs6','size'),negative_value_flag=('negative_value_flag','max'),negative_quantity_flag=('negative_quantity_flag','max'))
 d['source_archive']=archive;return d,{'archive':archive,'raw_rows':raw_rows,'selected_rows':selected_rows,'aggregated_rows':len(d)}

def main():
 p=argparse.ArgumentParser();p.add_argument('--year',type=int,required=True);p.add_argument('--out-dir',type=Path,required=True);a=p.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True);work=a.out_dir/'work';work.mkdir(exist_ok=True);records=[];frames=[]
 with requests.Session() as s:
  s.headers.update({'User-Agent':'academic-energy-intensive-products/1.0'});names=archive_names(listing(s),a.year)
  if not names:raise RuntimeError(f'No monthly archives for {a.year}')
  for i,name in enumerate(names,1):
   ap=work/name;rec={'archive':name}
   try:
    rec['archive_bytes']=download(s,name,ap);ed=work/f'extract_{i:02d}';ed.mkdir(exist_ok=True)
    with py7zr.SevenZipFile(ap,'r') as seven:rec['contained_files']=seven.getnames();seven.extractall(ed)
    rec['files']=[]
    for fp in [x for x in ed.rglob('*') if x.is_file()]:
     d,r=process(fp,name,a.year);rec['files'].append(r)
     if not d.empty:frames.append(d)
    shutil.rmtree(ed,ignore_errors=True);ap.unlink(missing_ok=True)
   except Exception as exc:rec['error']=repr(exc);ap.unlink(missing_ok=True)
   records.append(rec);print(json.dumps(rec,default=str),flush=True)
 if not frames:raise RuntimeError('No retained observations')
 panel=pd.concat(frames,ignore_index=True);panel=panel.groupby(['reporter','partner','month','cn8','hs6','product_family','energy_intensity_score','nace_r2'],as_index=False,dropna=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'),raw_rows=('raw_rows','sum'),negative_value_flag=('negative_value_flag','max'),negative_quantity_flag=('negative_quantity_flag','max'),source_archives=('source_archive',lambda v:'|'.join(sorted(set(v)))))
 panel['unit_value_eur_per_kg']=np.where(panel.quantity_kg.gt(0),panel.trade_value_eur/panel.quantity_kg,np.nan);panel=panel.sort_values(['month','reporter','partner','cn8']);out=a.out_dir/f'comext_energy_intensive_{a.year}.parquet';panel.to_parquet(out,index=False);panel.to_csv(a.out_dir/f'comext_energy_intensive_{a.year}.csv.gz',index=False,compression='gzip')
 summary={'year':a.year,'archives_available':len(names),'archives_with_errors':[r['archive'] for r in records if 'error'in r],'rows':len(panel),'reporters':panel.reporter.nunique(),'partners':panel.partner.nunique(),'months':panel.month.nunique(),'cn8_products':panel.cn8.nunique(),'hs6_products':panel.hs6.nunique(),'families':panel.product_family.nunique(),'first_month':str(panel.month.min()),'latest_month':str(panel.month.max()),'records':records};(a.out_dir/f'audit_{a.year}.json').write_text(json.dumps(summary,indent=2,default=str));shutil.rmtree(work,ignore_errors=True);print(json.dumps({k:v for k,v in summary.items() if k!='records'},indent=2),flush=True)
if __name__=='__main__':main()
