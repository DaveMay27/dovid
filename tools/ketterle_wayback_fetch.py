from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; KetterleNotesArchiver/1.0; educational use)"})

RLE_HOME = "https://www.rle.mit.edu/cua_pub/8.422/home.htm"


def rget(url, **kwargs):
    verify = False if (urlparse(url).hostname or '').endswith('rle.mit.edu') else True
    return S.get(url, timeout=60, allow_redirects=True, verify=verify, **kwargs)


def clean_label(a):
    return ' '.join(a.stripped_strings).replace('\r',' ').replace('\n',' ').strip()


def variants(url):
    p = urlparse(url)
    path = p.path
    if p.query:
        path += '?' + p.query
    hosts = ['www.rle.mit.edu', 'rle.mit.edu']
    schemes = ['http', 'https']
    out=[]
    for sch in schemes:
        for h in hosts:
            out.append(f'{sch}://{h}{path}')
    # preserve the exact source first
    return [url] + [x for x in out if x != url]


def cdx_snapshots(original):
    params = {
        'url': original,
        'output': 'json',
        'fl': 'timestamp,original,statuscode,mimetype,digest,length',
        'filter': ['statuscode:200'],
        'collapse': 'digest',
        'from': '2013',
        'to': '2026',
    }
    try:
        r = S.get('https://web.archive.org/cdx/search/cdx', params=params, timeout=60)
        if r.status_code != 200:
            return [], {'status': r.status_code, 'text': r.text[:300]}
        data = r.json()
        if not data or len(data) < 2:
            return [], {'status': 200, 'rows': 0}
        header=data[0]
        rows=[dict(zip(header,row)) for row in data[1:]]
        # Prefer PDFs and snapshots closest to 2017, then newest.
        rows.sort(key=lambda x: (0 if 'pdf' in x.get('mimetype','').lower() else 1,
                                 abs(int(x['timestamp'][:4])-2017), -int(x['timestamp'])))
        return rows, {'status': 200, 'rows': len(rows)}
    except Exception as e:
        return [], {'error': repr(e)}


def fetch_snapshot(original, timestamp):
    # id_ avoids Wayback rewriting the PDF payload.
    u = f'https://web.archive.org/web/{timestamp}id_/{original}'
    r = S.get(u, timeout=90, allow_redirects=True)
    return u, r


def timestamp_probes(original):
    # Fallback if CDX is blocked; Wayback will redirect to the closest snapshot.
    for year in [2017, 2018, 2019, 2016, 2015, 2020, 2021, 2014, 2013, 2022, 2023, 2024]:
        ts=f'{year}0101000000'
        u=f'https://web.archive.org/web/{ts}id_/{original}'
        try:
            r=S.get(u,timeout=90,allow_redirects=True)
            yield u,r
        except Exception:
            continue


def main():
    out=Path(sys.argv[1] if len(sys.argv)>1 else 'ketterle_wayback')
    out.mkdir(parents=True,exist_ok=True)
    pdir=out/'pdfs'; pdir.mkdir(exist_ok=True)

    home=rget(RLE_HOME); home.raise_for_status()
    (out/'rle_home.html').write_text(home.text,encoding='utf-8',errors='replace')
    soup=BeautifulSoup(home.text,'html.parser')
    targets=[]
    for a in soup.find_all('a',href=True):
        label=clean_label(a)
        u=urljoin(home.url,a['href'])
        path=unquote(urlparse(u).path)
        base=Path(path).name
        m=re.match(r'L(\d+)[ _-]',base,re.I)
        if not m:
            continue
        lec=int(m.group(1))
        if 3 <= lec <= 21 and '/Classroom files/' in path:
            targets.append({'lecture':lec,'label':label,'url':u,'basename':base})
    # unique URLs in source order
    uniq=[]; seen=set()
    for t in targets:
        if t['url'] not in seen:
            seen.add(t['url']); uniq.append(t)
    targets=uniq

    records=[]
    for idx,t in enumerate(targets,1):
        rec=dict(t)
        rec['attempts']=[]
        payload=None; chosen=None
        for var in variants(t['url']):
            rows,meta=cdx_snapshots(var)
            rec['attempts'].append({'variant':var,'cdx':meta,'snapshots_sample':rows[:3]})
            for row in rows[:8]:
                try:
                    snap_url,r=fetch_snapshot(row.get('original') or var,row['timestamp'])
                    att={'snapshot_url':snap_url,'status':r.status_code,'final_url':r.url,
                         'content_type':r.headers.get('content-type',''),'bytes':len(r.content),
                         'pdf_magic':r.content.startswith(b'%PDF')}
                    rec['attempts'].append(att)
                    if r.status_code==200 and r.content.startswith(b'%PDF'):
                        payload=r.content; chosen=att; break
                except Exception as e:
                    rec['attempts'].append({'snapshot_error':repr(e)})
            if payload is not None:
                break
        if payload is None:
            # fallback date probes across http/www variant first
            fallback=variants(t['url'])[1] if len(variants(t['url']))>1 else t['url']
            for snap_url,r in timestamp_probes(fallback):
                att={'probe_url':snap_url,'status':r.status_code,'final_url':r.url,
                     'content_type':r.headers.get('content-type',''),'bytes':len(r.content),
                     'pdf_magic':r.content.startswith(b'%PDF')}
                rec['attempts'].append(att)
                if r.status_code==200 and r.content.startswith(b'%PDF'):
                    payload=r.content; chosen=att; break
        if payload is not None:
            safe=re.sub(r'[^A-Za-z0-9._-]+','_',t['basename'])
            fn=f'{idx:02d}_L{t["lecture"]:02d}_{safe}'
            if not fn.lower().endswith('.pdf'): fn += '.pdf'
            (pdir/fn).write_bytes(payload)
            rec['downloaded']=True; rec['local_name']=fn; rec['chosen']=chosen; rec['bytes']=len(payload)
        else:
            rec['downloaded']=False
        records.append(rec)
        print(f'[{idx}/{len(targets)}] L{t["lecture"]}:', 'OK' if rec['downloaded'] else 'MISS', t['basename'])
        time.sleep(0.35)

    manifest={'source_home':RLE_HOME,'targets':targets,'records':records}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    summary={'targets':len(targets),'downloaded':sum(r['downloaded'] for r in records),
             'missing':[r['basename'] for r in records if not r['downloaded']]}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    main()
