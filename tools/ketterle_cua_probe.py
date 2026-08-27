from __future__ import annotations

import json, re, sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import requests, urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0'})
starts=[
 'http://cua.mit.edu/8.422_S13/',
 'https://cua.mit.edu/8.422_S13/',
 'http://www.cua.mit.edu/8.422_S13/',
 'https://www.cua.mit.edu/8.422_S13/',
 'http://rle.mit.edu/cua_pub/8.422_S13/',
 'https://rle.mit.edu/cua_pub/8.422_S13/',
 'http://www.rle.mit.edu/cua_pub/8.422_S13/',
 'https://www.rle.mit.edu/cua_pub/8.422_S13/',
]

def safe(s):
 s=unquote(s); s=re.sub(r'[^A-Za-z0-9._-]+','_',s); return s[:180] or 'file'

def get(u):
 return S.get(u,timeout=35,allow_redirects=True,verify=False)

def main():
 out=Path(sys.argv[1] if len(sys.argv)>1 else 'cua_probe'); out.mkdir(parents=True,exist_ok=True)
 records=[]; pdfs={}
 for i,u in enumerate(starts):
  rec={'start':u}
  try:
   r=get(u); rec.update(status=r.status_code,final_url=r.url,content_type=r.headers.get('content-type',''),bytes=len(r.content),head=r.text[:500] if 'text' in r.headers.get('content-type','') else '')
   if r.status_code==200 and 'text/html' in r.headers.get('content-type','').lower():
    (out/f'page_{i}.html').write_text(r.text,encoding='utf-8',errors='replace')
    soup=BeautifulSoup(r.text,'html.parser')
    rec['links']=[]
    for a in soup.find_all('a',href=True):
     lab=' '.join(a.stripped_strings).strip(); link=urljoin(r.url,a['href'])
     rec['links'].append({'text':lab,'url':link})
     if urlparse(link).path.lower().endswith('.pdf'):
      pdfs.setdefault(link,lab)
  except Exception as e: rec['error']=repr(e)
  records.append(rec)
 # If any page worked, follow note-like same-site HTML one level and gather PDFs.
 more=[]
 for rec in list(records):
  for x in rec.get('links',[]):
   link=x['url']; lab=x['text']; p=urlparse(link)
   if any(k in (p.path+' '+lab).lower() for k in ['note','lecture','class','2013','reading']) and not p.path.lower().endswith('.pdf'):
    try:
     r=get(link)
     m={'url':link,'status':r.status_code,'final_url':r.url,'content_type':r.headers.get('content-type',''),'bytes':len(r.content)}
     if r.status_code==200 and 'html' in r.headers.get('content-type','').lower():
      soup=BeautifulSoup(r.text,'html.parser')
      for a in soup.find_all('a',href=True):
       l=urljoin(r.url,a['href']); t=' '.join(a.stripped_strings).strip()
       if urlparse(l).path.lower().endswith('.pdf'): pdfs.setdefault(l,t)
     more.append(m)
    except Exception as e: more.append({'url':link,'error':repr(e)})
 # download discovered PDFs
 pdir=out/'pdfs'; pdir.mkdir(exist_ok=True); downloaded=[]
 for j,(u,lab) in enumerate(pdfs.items(),1):
  rr={'url':u,'label':lab}
  try:
   r=get(u); rr.update(status=r.status_code,final_url=r.url,content_type=r.headers.get('content-type',''),bytes=len(r.content),pdf_magic=r.content.startswith(b'%PDF'))
   if r.status_code==200 and r.content.startswith(b'%PDF'):
    fn=f'{j:03d}_{safe(Path(urlparse(r.url).path).name)}';
    if not fn.lower().endswith('.pdf'): fn+='.pdf'
    (pdir/fn).write_bytes(r.content); rr.update(downloaded=True,local_name=fn)
   else: rr['downloaded']=False
  except Exception as e: rr.update(downloaded=False,error=repr(e))
  downloaded.append(rr)
 manifest={'starts':records,'followed':more,'pdfs':downloaded}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
 summary={'working_starts':[r for r in records if r.get('status')==200],'pdf_candidates':len(downloaded),'downloaded':sum(bool(x.get('downloaded')) for x in downloaded)}
 (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({'working_start_count':len(summary['working_starts']),'pdf_candidates':len(downloaded),'downloaded':summary['downloaded']},indent=2))

if __name__=='__main__': main()
