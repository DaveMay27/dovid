from __future__ import annotations
import json, re, sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import requests, urllib3
from bs4 import BeautifulSoup
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0 (compatible; KetterleArchiveFetcher/1.1)'})
HOME='https://www.rle.mit.edu/cua_pub/8.422/home.htm'

def get(u,timeout=60,verify=True): return S.get(u,timeout=timeout,allow_redirects=True,verify=verify)
def label(a): return ' '.join(a.stripped_strings).strip()
def safe(s):
 s=unquote(s); s=re.sub(r'[^A-Za-z0-9._-]+','_',s); return s[:180] or 'file.pdf'

def main():
 out=Path(sys.argv[1] if len(sys.argv)>1 else 'wayback_fast'); out.mkdir(parents=True,exist_ok=True); pdir=out/'pdfs'; pdir.mkdir(exist_ok=True)
 h=get(HOME,verify=False); h.raise_for_status(); soup=BeautifulSoup(h.text,'html.parser')
 targets=[]
 for a in soup.find_all('a',href=True):
  u=urljoin(h.url,a['href']); base=Path(unquote(urlparse(u).path)).name; m=re.match(r'L(\d+)[ _-]',base,re.I)
  if m and 3<=int(m.group(1))<=21 and '/Classroom files/' in unquote(urlparse(u).path):
   targets.append({'lecture':int(m.group(1)),'label':label(a),'url':u,'basename':base})
 seen=set(); targets=[t for t in targets if not (t['url'] in seen or seen.add(t['url']))]
 # directory prefix from first target
 prefix=targets[0]['url'].rsplit('/',1)[0]+'/'
 variants=[]
 p=urlparse(prefix)
 for sch in ['http','https']:
  for host in ['www.rle.mit.edu','rle.mit.edu']:
   variants.append(f'{sch}://{host}{p.path}')
 rows=[]; cdx_attempts=[]
 for pref in variants:
  params={'url':pref,'matchType':'prefix','output':'json','fl':'timestamp,original,statuscode,mimetype,digest,length','filter':['statuscode:200'],'collapse':'digest','from':'2013','to':'2026'}
  try:
   r=S.get('https://web.archive.org/cdx/search/cdx',params=params,timeout=90)
   att={'prefix':pref,'status':r.status_code,'bytes':len(r.content),'head':r.text[:200]}; cdx_attempts.append(att)
   if r.status_code==200:
    data=r.json()
    if len(data)>1:
     hdr=data[0]; got=[dict(zip(hdr,x)) for x in data[1:]]; rows.extend(got); att['rows']=len(got)
  except Exception as e: cdx_attempts.append({'prefix':pref,'error':repr(e)})
 # index snapshots by decoded basename
 bybase={}
 for row in rows:
  b=Path(unquote(urlparse(row['original']).path)).name.lower()
  bybase.setdefault(b,[]).append(row)
 for lst in bybase.values():
  lst.sort(key=lambda x:(0 if 'pdf' in x.get('mimetype','').lower() else 1,abs(int(x['timestamp'][:4])-2017),-int(x['timestamp'])))
 recs=[]
 for i,t in enumerate(targets,1):
  rec=dict(t); choices=bybase.get(t['basename'].lower(),[]); rec['archive_choices']=choices[:5]; ok=False
  # if prefix result did not include exact target, ask CDX exact once
  if not choices:
   try:
    rr=S.get('https://web.archive.org/cdx/search/cdx',params={'url':t['url'],'output':'json','fl':'timestamp,original,statuscode,mimetype,digest,length','filter':'statuscode:200','collapse':'digest'},timeout=45)
    if rr.status_code==200:
     d=rr.json(); choices=[dict(zip(d[0],x)) for x in d[1:]] if len(d)>1 else []
     rec['exact_cdx_rows']=len(choices)
   except Exception as e: rec['exact_cdx_error']=repr(e)
  for row in choices[:8]:
   orig=row.get('original') or t['url']; ts=row['timestamp']; wu=f'https://web.archive.org/web/{ts}id_/{orig}'
   try:
    r=S.get(wu,timeout=60,allow_redirects=True); att={'wayback_url':wu,'status':r.status_code,'final':r.url,'bytes':len(r.content),'ct':r.headers.get('content-type',''),'pdf':r.content.startswith(b'%PDF')}; rec.setdefault('fetch_attempts',[]).append(att)
    if r.status_code==200 and r.content.startswith(b'%PDF'):
     fn=f'{i:02d}_L{t["lecture"]:02d}_{safe(t["basename"])}';
     if not fn.lower().endswith('.pdf'): fn+='.pdf'
     (pdir/fn).write_bytes(r.content); rec.update(downloaded=True,local_name=fn,bytes=len(r.content),chosen=att); ok=True; break
   except Exception as e: rec.setdefault('fetch_attempts',[]).append({'error':repr(e),'wayback_url':wu})
  if not ok: rec['downloaded']=False
  recs.append(rec); print(i,t['basename'],'OK' if ok else 'MISS',flush=True)
 manifest={'home':HOME,'prefix':prefix,'cdx_attempts':cdx_attempts,'row_count':len(rows),'targets':targets,'records':recs}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
 summ={'targets':len(targets),'downloaded':sum(x['downloaded'] for x in recs),'missing':[x['basename'] for x in recs if not x['downloaded']],'row_count':len(rows)}
 (out/'summary.json').write_text(json.dumps(summ,indent=2),encoding='utf-8'); print(json.dumps(summ,indent=2),flush=True)
if __name__=='__main__': main()
