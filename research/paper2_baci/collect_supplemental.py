#!/usr/bin/env python3
from __future__ import annotations
import json, zipfile
from pathlib import Path
import pandas as pd

OUT=Path('research_output/supplemental'); OUT.mkdir(parents=True,exist_ok=True)
GEOZIP=OUT/'dist_cepii.zip'; PINK=OUT/'CMO-Historical-Data-Monthly.xlsx'
FOCAL_SERIES=['Aluminum','Aluminium','Urea','DAP','TSP','Potassium chloride','Natural gas, Europe','Natural gas, US','Crude oil, Brent','Fertilizer','Energy']

def parse_geodist():
    with zipfile.ZipFile(GEOZIP) as z:
        names=z.namelist(); target=next((n for n in names if n.lower().endswith('.xls') or n.lower().endswith('.xlsx')),None)
        if target is None: raise RuntimeError(f'No Excel file in GeoDist archive: {names}')
        z.extract(target,OUT); path=OUT/target
    d=pd.read_excel(path)
    d.columns=[str(c).strip().lower() for c in d.columns]
    required={'iso_o','iso_d'}
    if not required.issubset(d.columns): raise RuntimeError(f'Unexpected GeoDist columns {d.columns.tolist()}')
    keep=[c for c in ['iso_o','iso_d','dist','distcap','distw','distwces','contig','comlang_off','comlang_ethno','colony','comcol','curcol'] if c in d.columns]
    d=d[keep].copy(); d['iso_o']=d.iso_o.astype(str).str.upper(); d['iso_d']=d.iso_d.astype(str).str.upper()
    d.to_csv(OUT/'dist_cepii.csv.gz',index=False,compression='gzip')
    return {'rows':len(d),'columns':keep,'origins':d.iso_o.nunique(),'destinations':d.iso_d.nunique()}

def parse_pink():
    xls=pd.ExcelFile(PINK)
    audit={'sheets':xls.sheet_names}
    # World Bank workbook convention: 'Monthly Prices' contains metadata rows before the table.
    sheet=next((s for s in xls.sheet_names if 'monthly' in s.lower()),xls.sheet_names[0])
    raw=pd.read_excel(PINK,sheet_name=sheet,header=None)
    header_row=None
    for i in range(min(20,len(raw))):
        vals=raw.iloc[i].astype(str).str.lower().tolist()
        if any(v in {'date','month'} or 'date'==v.strip() for v in vals):
            header_row=i; break
    if header_row is None:
        # Workbook normally uses row 5 (zero-indexed) for column names.
        header_row=4
    d=pd.read_excel(PINK,sheet_name=sheet,header=header_row)
    d=d.dropna(how='all'); d.columns=[str(c).strip() for c in d.columns]
    date_col=next((c for c in d.columns if c.lower() in {'date','month'} or 'date' in c.lower()),d.columns[0])
    d=d.rename(columns={date_col:'date'}); d['date']=pd.to_datetime(d.date,errors='coerce'); d=d[d.date.notna()].copy()
    # Keep all series in a clean file, plus a focused subset based on column-name matching.
    d.to_csv(OUT/'world_bank_pink_sheet_monthly_all.csv.gz',index=False,compression='gzip')
    cols=['date']+[c for c in d.columns if c!='date' and any(term.lower() in c.lower() for term in FOCAL_SERIES)]
    focused=d[cols].copy(); focused.to_csv(OUT/'world_bank_pink_sheet_focal_monthly.csv',index=False)
    audit.update({'sheet_used':sheet,'header_row_zero_indexed':header_row,'rows':len(d),'columns':d.columns.tolist(),'focused_columns':cols})
    return audit

def main():
    summary={'geodist':parse_geodist(),'pink_sheet':parse_pink()}
    (OUT/'supplemental_summary.json').write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps(summary,indent=2,default=str),flush=True)
if __name__=='__main__': main()
