#!/usr/bin/env python3
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pyfixest as pf

rng=np.random.default_rng(42)
rows=[]
# Four calendar years: treatment applies only to affected origins in the final
# year's March-April months, so it is not absorbed by origin-calendar-month or
# origin-year fixed effects.
months=pd.date_range('2023-01-01','2026-04-01',freq='MS')
for i in range(20):
 for o in range(15):
  for t,month in enumerate(months):
   x=int(o<3 and month in [pd.Timestamp('2026-03-01'),pd.Timestamp('2026-04-01')])
   eta=0.4*x+0.2*np.sin(month.month/2)+0.05*(month.year-2023)+rng.normal(0,.2)
   y=rng.poisson(np.exp(eta))
   rows.append({'i':f'i{i}','o':f'o{o}','t':t,'month':month,'year':month.year,'calmonth':month.month,'x':x,'y':y,'asinh_y':np.arcsinh(y)})
d=pd.DataFrame(rows)
d['rel']=d.i+'_'+d.o
d['it']=d.i+'_'+d.month.dt.strftime('%Y%m')
d['oy']=d.o+'_'+d.year.astype(str)
d['ocm']=d.o+'_'+d.calmonth.astype(str)
out=Path('output/pyfixest'); out.mkdir(parents=True,exist_ok=True)
res=[]
for name,func,formula in [
 ('ols',pf.feols,'asinh_y ~ x | rel + it + oy + ocm'),
 ('ppml',pf.fepois,'y ~ x | rel + it + oy + ocm')]:
 try:
  fit=func(formula,data=d,vcov={'CRV1':'i + o'})
  tidy=fit.tidy()
  if hasattr(tidy,'to_pandas'): tidy=tidy.to_pandas()
  tidy.to_csv(out/f'{name}_tidy.csv')
  res.append({'model':name,'status':'ok','tidy':tidy.reset_index().to_dict('records'),'repr':str(fit)[:1000]})
 except Exception as e:
  res.append({'model':name,'status':'failed','error':repr(e)})
(out/'result.json').write_text(json.dumps(res,indent=2,default=str)); print(json.dumps(res,indent=2,default=str))
if any(r['status']!='ok' for r in res): raise SystemExit(1)
