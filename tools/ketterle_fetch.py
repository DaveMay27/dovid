from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; KetterleNotesFetcher/1.0; educational archival use)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

COURSES = {
    "8421": {
        "start": "https://www.rle.mit.edu/cua_pub/8.421_S16/",
        "allowed_prefixes": ["/cua_pub/8.421", "/cua_pub/8.421_S16"],
    },
    "8422": {
        "start": "https://www.rle.mit.edu/cua_pub/8.422/home.htm",
        "allowed_prefixes": ["/cua_pub/8.422"],
    },
}


def fetch(url: str) -> requests.Response:
    r = SESSION.get(url, timeout=45, allow_redirects=True)
    r.raise_for_status()
    return r


def norm_host(host: str | None) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_rle(url: str) -> bool:
    return norm_host(urlparse(url).hostname) == "rle.mit.edu"


def sanitize_name(name: str) -> str:
    name = requests.utils.unquote(name)
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name[:180] or "file.pdf"


def anchor_text(a) -> str:
    return " ".join(a.stripped_strings).strip()


def crawl_course(key: str, cfg: dict, out_root: Path) -> dict:
    start = cfg["start"]
    course_dir = out_root / key
    raw_dir = course_dir / "raw_pdfs"
    html_dir = course_dir / "html"
    raw_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)

    queue = [(start, 0, "START")]
    seen_pages = set()
    pdf_candidates = []
    pages = []

    while queue and len(seen_pages) < 100:
        url, depth, via = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            r = fetch(url)
        except Exception as e:
            pages.append({"url": url, "depth": depth, "via": via, "error": repr(e)})
            continue

        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype.lower() or r.content.startswith(b"%PDF"):
            pdf_candidates.append({"url": r.url, "anchor_text": via, "source_page": "direct-crawl"})
            continue

        text = r.text
        page_name = sanitize_name(urlparse(r.url).path.rsplit("/", 1)[-1] or "index.html")
        (html_dir / f"{len(pages):03d}_{page_name}.html").write_text(text, encoding="utf-8", errors="replace")
        pages.append({"url": r.url, "depth": depth, "via": via, "status": r.status_code, "content_type": ctype})

        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a", href=True):
            txt = anchor_text(a)
            href = urljoin(r.url, a["href"])
            p = urlparse(href)
            if p.scheme not in {"http", "https"}:
                continue
            clean = href.split("#", 1)[0]
            if p.path.lower().endswith(".pdf") or ".pdf?" in href.lower():
                pdf_candidates.append({"url": clean, "anchor_text": txt, "source_page": r.url})
                continue
            noteish = any(t in (txt + " " + p.path).lower() for t in [
                "class note", "lecture note", "notes", "2013", "2014", "write-up", "writeup"
            ])
            allowed_path = any(p.path.startswith(pref) for pref in cfg["allowed_prefixes"])
            if depth < 3 and same_rle(clean) and allowed_path and noteish and clean not in seen_pages:
                queue.append((clean, depth + 1, txt or p.path))

    by_url = {}
    for item in pdf_candidates:
        u = item["url"]
        if u not in by_url:
            by_url[u] = {**item, "anchor_texts": [], "source_pages": []}
        if item.get("anchor_text") and item["anchor_text"] not in by_url[u]["anchor_texts"]:
            by_url[u]["anchor_texts"].append(item["anchor_text"])
        if item.get("source_page") and item["source_page"] not in by_url[u]["source_pages"]:
            by_url[u]["source_pages"].append(item["source_page"])

    downloaded = []
    for idx, item in enumerate(by_url.values(), 1):
        url = item["url"]
        record = dict(item)
        record["same_rle"] = same_rle(url)
        if not same_rle(url):
            record["downloaded"] = False
            record["skip_reason"] = "external host"
            downloaded.append(record)
            continue
        try:
            r = fetch(url)
            is_pdf = r.content.startswith(b"%PDF") or "pdf" in r.headers.get("content-type", "").lower()
            record.update({"final_url": r.url, "status": r.status_code,
                           "content_type": r.headers.get("content-type", ""),
                           "bytes": len(r.content), "pdf_magic": r.content.startswith(b"%PDF")})
            if is_pdf:
                base = sanitize_name(Path(urlparse(r.url).path).name or f"candidate_{idx:03d}.pdf")
                if not base.lower().endswith(".pdf"):
                    base += ".pdf"
                fn = f"{idx:03d}_{base}"
                (raw_dir / fn).write_bytes(r.content)
                record["downloaded"] = True
                record["local_name"] = fn
            else:
                record["downloaded"] = False
                record["skip_reason"] = "not a PDF response"
        except Exception as e:
            record["downloaded"] = False
            record["error"] = repr(e)
        downloaded.append(record)

    manifest = {"course": key, "start_url": start, "pages_crawled": pages, "pdf_candidates": downloaded}
    (course_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def try_ocw_zip(course_key: str, out_root: Path):
    slugs = {"8421": "8-421-atomic-and-optical-physics-i-spring-2014",
             "8422": "8-422-atomic-and-optical-physics-ii-spring-2013"}
    slug = slugs[course_key]
    download_page = f"https://ocw.mit.edu/courses/{slug}/download/"
    records = []
    try:
        r = fetch(download_page)
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            u = urljoin(r.url, a["href"])
            txt = anchor_text(a)
            if ".zip" in u.lower() or "download course" in txt.lower():
                links.append((u, txt))
        if not links:
            for a in soup.find_all("a", href=True):
                txt = anchor_text(a)
                if "download" in txt.lower():
                    links.append((urljoin(r.url, a["href"]), txt))
        for i, (u, txt) in enumerate(links, 1):
            rec = {"url": u, "anchor_text": txt}
            try:
                rr = fetch(u)
                rec.update({"final_url": rr.url, "status": rr.status_code,
                            "content_type": rr.headers.get("content-type", ""), "bytes": len(rr.content)})
                if rr.content[:4] == b"PK\x03\x04" or "zip" in rr.headers.get("content-type", "").lower():
                    dest = out_root / course_key / f"ocw_course_package_{i}.zip"
                    dest.write_bytes(rr.content)
                    rec["downloaded"] = True
                    rec["local_name"] = dest.name
                else:
                    rec["downloaded"] = False
            except Exception as e:
                rec["error"] = repr(e)
            records.append(rec)
    except Exception as e:
        records.append({"download_page": download_page, "error": repr(e)})
    (out_root / course_key / "ocw_download_attempts.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records


def main():
    out_root = Path(sys.argv[1] if len(sys.argv) > 1 else "ketterle_raw")
    out_root.mkdir(parents=True, exist_ok=True)
    summary = {}
    for key, cfg in COURSES.items():
        m = crawl_course(key, cfg, out_root)
        z = try_ocw_zip(key, out_root)
        summary[key] = {"pages": len(m["pages_crawled"]), "pdf_candidates": len(m["pdf_candidates"]),
                        "downloaded_pdfs": sum(bool(x.get("downloaded")) for x in m["pdf_candidates"]),
                        "ocw_zip_records": z}
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
