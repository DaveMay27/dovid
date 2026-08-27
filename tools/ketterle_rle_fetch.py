from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; KetterleNotesFetcher/2.0; educational archival use)"})

COURSES = {
    "8421": "https://www.rle.mit.edu/cua_pub/8.421_S16/",
    "8422": "https://www.rle.mit.edu/cua_pub/8.422/home.htm",
}


def rle_host(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return h in {"rle.mit.edu", "www.rle.mit.edu"}


def get(url: str):
    return S.get(url, timeout=60, allow_redirects=True, verify=False if rle_host(url) else True)


def safe_name(s: str) -> str:
    s = requests.utils.unquote(s)
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", s)
    s = re.sub(r"\s+", "_", s).strip("._")
    return s[:180] or "file"


def text(a) -> str:
    return " ".join(a.stripped_strings).strip()


def relevant_html_link(url: str, label: str, course: str) -> bool:
    p = urlparse(url)
    if not rle_host(url):
        return False
    hay = (p.path + " " + label).lower()
    root = "/cua_pub/8.421" if course == "8421" else "/cua_pub/8.422"
    return p.path.startswith(root) and any(k in hay for k in [
        "note", "2013", "2014", "write", "lecture", "class", "resonance", "atom", "coherence",
        "broadening", "photon", "qed", "light", "casimir", "bloch", "dressed", "temperature", "bose", "fermi", "ion"
    ])


def crawl(course: str, start: str, out: Path):
    cdir = out / course
    hdir = cdir / "html"
    pdir = cdir / "pdfs"
    hdir.mkdir(parents=True, exist_ok=True)
    pdir.mkdir(parents=True, exist_ok=True)

    q = [(start, 0, "START")]
    seen = set()
    pages = []
    candidates = {}

    while q and len(seen) < 120:
        url, depth, via = q.pop(0)
        url = url.split("#", 1)[0]
        if url in seen:
            continue
        seen.add(url)
        try:
            r = get(url)
            r.raise_for_status()
        except Exception as e:
            pages.append({"url": url, "depth": depth, "via": via, "error": repr(e)})
            continue
        ct = r.headers.get("content-type", "")
        if r.content.startswith(b"%PDF") or "application/pdf" in ct.lower():
            candidates.setdefault(r.url, {"url": r.url, "labels": [], "sources": []})
            candidates[r.url]["labels"].append(via)
            candidates[r.url]["sources"].append("direct")
            continue
        html = r.text
        fn = f"{len(pages):03d}_{safe_name(Path(urlparse(r.url).path).name or 'index')}.html"
        (hdir / fn).write_text(html, encoding="utf-8", errors="replace")
        pages.append({"url": r.url, "depth": depth, "via": via, "status": r.status_code, "content_type": ct, "local_html": fn})
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            lab = text(a)
            link = urljoin(r.url, a["href"]).split("#", 1)[0]
            pp = urlparse(link)
            if pp.scheme not in {"http", "https"}:
                continue
            is_pdf = pp.path.lower().endswith(".pdf") or ".pdf?" in link.lower()
            if is_pdf:
                ent = candidates.setdefault(link, {"url": link, "labels": [], "sources": []})
                if lab and lab not in ent["labels"]:
                    ent["labels"].append(lab)
                if r.url not in ent["sources"]:
                    ent["sources"].append(r.url)
            elif depth < 3 and relevant_html_link(link, lab, course) and link not in seen:
                q.append((link, depth + 1, lab or pp.path))

    records = []
    for i, ent in enumerate(candidates.values(), 1):
        rec = dict(ent)
        rec["same_rle"] = rle_host(ent["url"])
        if not rle_host(ent["url"]):
            rec["downloaded"] = False
            rec["skip_reason"] = "external"
            records.append(rec)
            continue
        try:
            r = get(ent["url"])
            r.raise_for_status()
            rec["final_url"] = r.url
            rec["bytes"] = len(r.content)
            rec["content_type"] = r.headers.get("content-type", "")
            rec["pdf_magic"] = r.content.startswith(b"%PDF")
            if r.content.startswith(b"%PDF"):
                base = safe_name(Path(urlparse(r.url).path).name)
                if not base.lower().endswith(".pdf"):
                    base += ".pdf"
                fn = f"{i:03d}_{base}"
                (pdir / fn).write_bytes(r.content)
                rec["downloaded"] = True
                rec["local_name"] = fn
            else:
                rec["downloaded"] = False
                rec["skip_reason"] = "response not PDF"
        except Exception as e:
            rec["downloaded"] = False
            rec["error"] = repr(e)
        records.append(rec)

    manifest = {"course": course, "start": start, "pages": pages, "pdfs": records}
    (cdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ketterle_rle")
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for course, start in COURSES.items():
        m = crawl(course, start, out)
        summary[course] = {"pages": len(m["pages"]), "candidates": len(m["pdfs"]), "downloaded": sum(bool(x.get("downloaded")) for x in m["pdfs"])}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
