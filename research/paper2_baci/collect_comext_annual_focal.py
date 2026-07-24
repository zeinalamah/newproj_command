#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,re,shutil,time
from pathlib import Path
import pandas as pd, py7zr, requests
BASE='https://ec.europa.eu/eurostat/api/dissemination/files'
EU27={'AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE'}
FOCAL={'281410','281420','760110','760120'}
ALIASES={'reporter':['REPORTER','DECLARANT','DECLARANT_ISO'],'partner':['PARTNER','PARTNER_ISO'],'product':['PRODUCT_NC','PRODUCT','CN8','NC8'],'flow':['FLOW','TRADE_FLOW'],'period':['PERIOD','TIME_PERIOD','MONTH'],'value':['VALUE_EUR','VALUE_IN_EUROS','TRADE_VALUE_EUR','VALUE'],'quantity':['QUANTITY_KG','QUANTITY_IN_KG','NET_MASS','QUANTITY']}

def download(url,path):
  with requests.Session() as s:
    s.headers.update({'User-Agent':'academic-hormuz-energy-research/1.0'})
    last=None
    for a in range(8):
      try:
        r=s.get(BASE,params={'file':url},stream=True,timeout=900); r.raise_for_status()
        with path.open('wb') as f:
          for chunk in r.iter_content(1024*1024):
            if chunk:f.write(chunk)
        return {'status':'ok','bytes':path.stat().st_size,'attempts':a+1,'remote':url}
      except Exception as e:last=repr(e);time.sleep(min(90,2**a))
  return {'status':'failed','error':last,'remote':url}

def delimiter(path):
  text=path.read_bytes()[:131072].decode('utf-8-sig',errors='replace')
  try:return csv.Sniffer().sniff('\n'.join(text.splitlines()[:10]),delimiters=',;\t|').delimiter
  except:return max([',',';','\t','|'],key=text.count)

def mapping(path,sep):
  cols=list(pd.read_csv(path,sep=sep,nrows=0,encoding='utf-8-sig').columns)
  norm={re.sub(r'\s+','_',str(c).strip().upper()):c for c in cols}; m={}
  for concept,cands in ALIASES.items():
    for c in cands:
      if c in norm:m[concept]=norm[c];break
  missing=set(ALIASES)-set(m)
  if missing:raise RuntimeError(f'missing {missing}; columns={cols}')
  return cols,m

def main():
  ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args();a.out_dir.mkdir(parents=True,exist_ok=True)
  arc=a.out_dir/f'full_v2_{a.year}52.7z';remote=f'comext/COMEXT_DATA/PRODUCTS/full_v2_{a.year}52.7z';log=download(remote,arc)
  frames=[];files=[]
  if log['status']=='ok':
    ext=a.out_dir/'extract';ext.mkdir(exist_ok=True)
    with py7zr.SevenZipFile(arc) as z:z.extractall(ext)
    for p in ext.rglob('*'):
      if not p.is_file():continue
      try:
        sep=delimiter(p);cols,m=mapping(p,sep);use=sorted(set(m.values()));raw=0;kept=0;chunks=[]
        for ch in pd.read_csv(p,sep=sep,usecols=use,dtype=str,chunksize=600000,encoding='utf-8-sig',low_memory=False):
          raw+=len(ch);x=ch.rename(columns={v:k for k,v in m.items()});prod=x['product'].fillna('').str.replace(r'\.0$','',regex=True).str.zfill(8);hs=prod.str[:6];rep=x.reporter.fillna('').str.upper().str.strip();par=x.partner.fillna('').str.upper().str.strip();flow=x.flow.fillna('').str.upper().str.strip();imp=flow.isin({'1','01','IMPORT','IMP','I'})|flow.str.startswith('1.')
          mask=rep.isin(EU27)&par.str.fullmatch(r'[A-Z]{2}')&hs.isin(FOCAL)&imp
          if mask.any():
            y=pd.DataFrame({'reporter':rep[mask],'partner':par[mask],'month':pd.to_datetime(x.loc[mask,'period'].str.extract(r'(\d{6})',expand=False)+'01',format='%Y%m%d',errors='coerce'),'cn8':prod[mask],'hs6':hs[mask],'trade_value_eur':pd.to_numeric(x.loc[mask,'value'],errors='coerce'),'quantity_kg':pd.to_numeric(x.loc[mask,'quantity'],errors='coerce')})
            y=y[y.month.dt.year.eq(a.year)];kept+=len(y);chunks.append(y)
        if chunks:frames.append(pd.concat(chunks,ignore_index=True))
        files.append({'file':p.name,'delimiter':repr(sep),'columns':cols,'mapping':m,'raw_rows':raw,'retained_rows':kept})
      except Exception as e:files.append({'file':p.name,'error':repr(e)})
    shutil.rmtree(ext,ignore_errors=True);arc.unlink(missing_ok=True)
  if frames:
    d=pd.concat(frames,ignore_index=True).groupby(['reporter','partner','month','cn8','hs6'],as_index=False).agg(trade_value_eur=('trade_value_eur','sum'),quantity_kg=('quantity_kg','sum'),source_rows=('hs6','size'))
  else:d=pd.DataFrame(columns=['reporter','partner','month','cn8','hs6','trade_value_eur','quantity_kg','source_rows'])
  d.to_csv(a.out_dir/f'comext_focal_{a.year}.csv.gz',index=False,compression='gzip')
  summary={'year':a.year,'download':log,'rows':len(d),'reporters':d.reporter.nunique(),'partners':d.partner.nunique(),'products':d.hs6.nunique(),'first_month':str(d.month.min()) if len(d) else None,'latest_month':str(d.month.max()) if len(d) else None,'files':files}
  (a.out_dir/f'audit_{a.year}.json').write_text(json.dumps(summary,indent=2,default=str));print(json.dumps({k:v for k,v in summary.items() if k!='files'},indent=2,default=str),flush=True)
  if log['status']!='ok' or d.empty:raise RuntimeError('Comext annual build failed or empty')
if __name__=='__main__':main()
