import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

ASX_HOME = "https://www.asx.com.au/"
ASX_TODAY_HTML = "https://www.asx.com.au/asx/v2/statistics/todayAnns.do"
ASX_ANNOUNCEMENTS_SEARCH = "https://www.asx.com.au/asx/v2/statistics/announcements.do"
RELEASE_TAG = "asx-pdf-cache"
RELEASE_NAME = "ASX PDF Cache"
RETENTION_DAYS = 14

TICKERS = set("""
29M TGP AX8 ADG ADM AEM AIS A1G AGL ALK ALS AMC AMP ALD ANX ANZ APA ARU ARV AS1 AR1 AAR ALX AWJ AZJ AUE ASL AUC AST AQD AUZ AR3 BM1 BAP BGD BCI BPT BCN BGL BHP BKB BC8 BTR BSA CAY CMM CAR CVV CRB CVN CYL CWP CGN CHN CEL CGF CIA CSC CIM CBA CCP CTI CYM DCN DTG DTR DVP DEV DGO DOW DPM DUE E25 EMR ERM EMN EVN FAL FFM FRE FMR FML FMG GMD GPR GL1 GOR GMR GG8 GPT GNG GRR GGP GT1 GSR HMG HAS HAV HRR HRN IGO ILU IFM IFL IVR IPH IRD KSC KAU KZR KAL KAR KGL KCN KNB KTA KGD LRV LRS LM1 LEG LGL LTR LKY LYD LYC MAC MGD MQG MHC MKR MRT MEU MMA MZZ MAT MXR MBG MM8 MEK MEG MIN MI6 MGR MND MGX MGV NAB NHC NMG NEM NXM NTU NST NVA NRW NGY NUG ODY OSH OM1 OBM OAU ORI ORG OZL OZM PGO PNR PMT PC2 PRN PRU PGL PLS PNX POL PDI PRX PRL QAN QUB QRL RMS REA RED RMX RVR RRL RSG RIO RGL RXL SFR SMI STO STN SYA SEN SVW SHN SLH SLR SVL SGC SNG SLS S32 SX2 SKI SPR SRG SBM SGP STK SRK SS1 SYD TLM TBN TAM TEX TMT TM1 TSO TGM TMZ THR TTM TOK TOR TRE TKL TCL TBR TCG TGN USL VAL VAU VMC VHM VTM VMM VML VTH VEA WES WAF WWI WGR WGX WBC WRM WIA WDS WOR YMG ZAG
""".split())

PEOPLE_KEYWORDS = ["appointment", "appointed", "appoints", "resignation", "resigns", "retirement", "retires", "company secretary", "secretary change", "ceo", "chief executive", "cfo", "chief financial", "coo", "chief operating", "managing director", "executive director", "non-executive director", "non executive director", "board change", "board appointment", "director appointment", "chair", "chairman", "chairwoman"]
PROJECT_SIGNAL_KEYWORDS = ["project acquisition", "acquisition", "acquire", "asset acquisition", "farm-in", "farm in", "joint venture", "jv", "development update", "project update", "construction", "site works", "commissioning", "restart", "expansion", "fid", "final investment decision", "dfs", "definitive feasibility", "pfs", "pre-feasibility", "prefeasibility", "feasibility", "study update", "scoping study", "maiden resource", "mineral resource", "ore reserve", "reserve update", "mining lease", "environmental approval", "epa approval", "approval", "permitting", "operations update", "production update", "drilling", "high-grade", "high grade", "re-entry", "reentry", "recommissioning", "site establishment", "underground mining contract", "dewatering", "project"]
FUNDING_SIGNAL_KEYWORDS = ["capital raising", "placement", "share purchase plan", "spp", "debt facility", "funding package", "strategic investment", "cornerstone investment", "offtake", "royalty", "streaming", "grant", "government grant", "financing"]
PRESENTATION_SIGNAL_KEYWORDS = ["investor presentation", "ceo presentation", "corporate presentation", "analyst presentation", "presentation"]
NOISE_KEYWORDS = ["appendix 3x", "appendix 3y", "change of director", "director interest", "director's interest", "initial director", "notice of meeting", "agm", "annual general meeting", "results of meeting", "cleansing notice", "section 708a", "notification of cessation", "issue of securities", "quotation of securities", "application for quotation", "substantial holder", "substantial holding", "becoming a substantial holder", "ceasing to be a substantial holder", "change in substantial holding", "notification regarding unquoted securities", "update - notification of buy-back", "buy-back", "form 10-q", "form 3 as filed", "bidder's statement"]


def headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-AU,en;q=0.9",
        "Referer": ASX_HOME,
    }


def is_signal(headline: str) -> bool:
    h = (headline or "").lower().strip()
    if not h or any(k in h for k in NOISE_KEYWORDS):
        return False
    return any(k in h for k in PRESENTATION_SIGNAL_KEYWORDS + PEOPLE_KEYWORDS + FUNDING_SIGNAL_KEYWORDS + PROJECT_SIGNAL_KEYWORDS)


def parse_date(s: str) -> str:
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        return s.strip()


def absolutise(url: str) -> str:
    return url if url.startswith("http") else f"https://www.asx.com.au{url}"


def extract_items(html: str, forced_ticker: str = ""):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    page_text = soup.get_text(" ", strip=True)
    for a in soup.select("a[href*='displayAnnouncement.do']"):
        href = a.get("href") or ""
        headline = " ".join(a.get_text(" ", strip=True).split())
        m = re.search(r"idsId=(\d+)", href)
        if not m:
            continue
        container = a.find_parent("tr") or a.find_parent()
        context = container.get_text(" ", strip=True) if container else page_text
        ticker = forced_ticker
        date_iso = ""
        if not ticker:
            md = re.search(r"\b([A-Z0-9]{2,5})\b\s+(\d{2}/\d{2}/\d{4})", context)
            if md:
                ticker = md.group(1).strip().upper()
                date_iso = parse_date(md.group(2))
        else:
            md = re.search(r"(\d{2}/\d{2}/\d{4})", context)
            if md:
                date_iso = parse_date(md.group(1))
        out.append({"id": m.group(1), "ticker": ticker, "date": date_iso, "headline": headline, "display_url": absolutise(href)})
    return list({x["id"]: x for x in out}.values())


def resolve_pdf_url(client: httpx.Client, display_url: str) -> Optional[str]:
    for url in [display_url, display_url + ("&" if "?" in display_url else "?") + "display=pdf"]:
        try:
            r = client.get(url, headers={**headers(), "Accept": "application/pdf,text/html,*/*"})
            content = r.content or b""
            if content.lstrip().startswith(b"%PDF"):
                return str(r.url)
            html = r.text or ""
            m = re.search(r'https?://[^"\']*announcements\.asx\.com\.au/[^"\']+\.pdf', html, re.I)
            if m:
                return m.group(0)
            m = re.search(r'["\']([^"\']*asxpdf/[^"\']+\.pdf)["\']', html, re.I)
            if m:
                return absolutise(m.group(1))
            soup = BeautifulSoup(html, "html.parser")
            candidates = []
            for tag in soup.select("a[href],iframe[src],embed[src],object[data]"):
                for attr in ("href", "src", "data"):
                    val = (tag.get(attr) or "").strip()
                    if val and ".pdf" in val.lower():
                        candidates.append(val)
            if candidates:
                candidates.sort(key=lambda x: ("asxpdf" not in x.lower(), len(x)))
                return absolutise(candidates[0])
        except Exception:
            pass
    return None


def download_pdf(client: httpx.Client, pdf_url: str) -> Optional[bytes]:
    try:
        r = client.get(pdf_url, headers={**headers(), "Accept": "application/pdf,*/*"})
        data = (r.content or b"").lstrip()
        if data.startswith(b"%PDF"):
            return data
    except Exception:
        pass
    return None


def asset_name(date_val: str, ticker: str, pdf_url: str) -> str:
    date_part = re.sub(r"[^0-9-]", "", date_val) or "ASX"
    ticker_part = re.sub(r"[^A-Za-z0-9]", "", ticker) or "ASX"
    m = re.search(r"/([^/]+)\.pdf(?:\?|$)", pdf_url, re.I)
    pdf_id = m.group(1)[-6:] if m else "pdf"
    return f"{date_part}_{ticker_part}_{pdf_id}.pdf"[:80]


def gh_headers():
    token = os.environ["GITHUB_TOKEN"]
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "asx-pdf-cache"}


def ensure_release(client: httpx.Client, repo: str):
    r = client.get(f"https://api.github.com/repos/{repo}/releases/tags/{RELEASE_TAG}", headers=gh_headers())
    if r.status_code == 404:
        r = client.post(f"https://api.github.com/repos/{repo}/releases", headers=gh_headers(), json={"tag_name": RELEASE_TAG, "name": RELEASE_NAME, "body": "Temporary public copies of ASX announcement PDFs. Assets older than 14 days are removed automatically.", "draft": False, "prerelease": True})
    r.raise_for_status()
    return r.json()


def list_assets(client: httpx.Client, repo: str, release_id: int):
    assets = []
    for page in range(1, 10):
        r = client.get(f"https://api.github.com/repos/{repo}/releases/{release_id}/assets", headers=gh_headers(), params={"per_page": 100, "page": page})
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch:
            break
        assets.extend(batch)
        if len(batch) < 100:
            break
    return assets


def cleanup(client: httpx.Client, repo: str, assets):
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    for a in assets:
        try:
            created = datetime.fromisoformat(str(a.get("created_at", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        if created < cutoff:
            client.delete(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}", headers=gh_headers())


def cache_items(items, client: httpx.Client, repo: str, release, existing_names: set[str]):
    upload_url = str(release["upload_url"]).split("{")[0]
    added = 0
    for item in items:
        ticker = item.get("ticker", "").upper()
        if ticker not in TICKERS or not is_signal(item.get("headline", "")):
            continue
        pdf_url = resolve_pdf_url(client, item["display_url"])
        if not pdf_url:
            continue
        name = asset_name(item.get("date", ""), ticker, pdf_url)
        if name in existing_names:
            continue
        pdf = download_pdf(client, pdf_url)
        if not pdf:
            continue
        h = gh_headers()
        h["Content-Type"] = "application/pdf"
        r = client.post(upload_url, headers=h, params={"name": name}, content=pdf)
        if r.status_code == 201:
            existing_names.add(name)
            added += 1
            print(f"cached {ticker} {name}")
        else:
            print(f"upload failed {name}: {r.status_code} {r.text[:160]}")
    return added


def today_items(client: httpx.Client):
    try:
        client.get(ASX_HOME, headers=headers())
    except Exception:
        pass
    for attempt in range(1, 5):
        try:
            r = client.get(ASX_TODAY_HTML, headers=headers())
            if r.status_code == 200 and len(r.text or "") > 10000:
                return extract_items(r.text)
        except Exception:
            pass
        time.sleep(attempt * 2)
    return []


def backfill_items(client: httpx.Client):
    cutoff = (datetime.now().date() - timedelta(days=7))
    all_items = {}
    for i, ticker in enumerate(sorted(TICKERS), start=1):
        try:
            r = client.get(ASX_ANNOUNCEMENTS_SEARCH, headers=headers(), params={"asxCode": ticker, "by": "asxCode", "timeframe": "D", "period": "W"})
            if r.status_code == 200:
                for item in extract_items(r.text, ticker):
                    try:
                        d = datetime.strptime(item.get("date", ""), "%Y-%m-%d").date()
                    except Exception:
                        continue
                    if d >= cutoff and is_signal(item.get("headline", "")):
                        all_items[item["id"]] = item
        except Exception as exc:
            print(f"backfill {ticker} failed: {exc}")
        if i % 25 == 0:
            print(f"backfill scanned {i}/{len(TICKERS)} tickers")
        time.sleep(0.2)
    return list(all_items.values())


def main():
    repo = os.getenv("GITHUB_REPOSITORY", "Cplatts1977/asx-gold-watcher")
    do_backfill = "--backfill" in sys.argv
    timeout = httpx.Timeout(90.0, connect=30.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        release = ensure_release(client, repo)
        assets = list_assets(client, repo, int(release["id"]))
        cleanup(client, repo, assets)
        existing = {str(a.get("name") or "") for a in assets}
        items = backfill_items(client) if do_backfill else today_items(client)
        added = cache_items(items, client, repo, release, existing)
        print(f"ASX public cache complete: {added} new PDFs from {len(items)} candidate announcements")


if __name__ == "__main__":
    main()
