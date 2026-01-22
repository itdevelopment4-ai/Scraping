#!/usr/bin/env python3
"""
Overnight movie/film company finder.

Searches DuckDuckGo for movie/film production + sound companies,
extracts a company name + website, and saves to CSV.

This script is designed to run overnight and stop once it reaches
TARGET_COUNT companies (default: 500).
"""

import csv
import logging
import os
import random
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ==========================
# CONFIG
# ==========================

OUTPUT_FILE = "movie_production_companies.csv"
LOG_FILE = "movie_company_search.log"

TARGET_COUNT = 500
MAX_PAGES = 10
SLEEP_MIN = 1.5
SLEEP_MAX = 3.5

REQUEST_TIMEOUT = 25
MAX_RESPONSE_BYTES = 2_000_000  # 2 MB

COUNTRIES = [
    "USA", "UK", "India", "Canada", "Australia",
    "Germany", "France", "Italy", "Spain",
    "UAE", "South Africa", "New Zealand", "Ireland",
    "Netherlands", "Sweden", "Norway", "Denmark",
    "Mexico", "Brazil", "Japan", "South Korea",
]

BASE_QUERIES = [
    "movie production company",
    "film production company",
    "movie production companies",
    "film production companies",
    "movie studio",
    "film studio",
    "motion picture production company",
    "film production services company",
    "film production house",
    "movie production house",
    "independent film production company",
    "film and video production company",
    "movie production service company",
    "film production agency",
    "movie sound production company",
    "film sound production company",
    "movie sound studio",
    "film sound studio",
    "post production sound company",
    "film post production company",
    "movie post production company",
    "audio post production for film",
    "post production studio",
]

BLOCKED_DOMAINS = [
    "wikipedia.org",
    "imdb.com",
    "britannica.com",
    "youtube.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "reddit.com",
    "pinterest.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "simplyhired.com",
    "careerbuilder.com",
    "crunchbase.com",
    "pitchbook.com",
    "zoominfo.com",
    "opencorporates.com",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


# ==========================
# LOGGING
# ==========================

logger = logging.getLogger("movie_company_search")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
)

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


# ==========================
# PARSERS
# ==========================

class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if not href:
            return
        self._current = {
            "href": href,
            "text": "",
            "attrs": attr_map,
        }

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""
        self.site_name = None

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l == "title":
            self.in_title = True
            return

        if tag_l != "meta":
            return

        attr_map = {k.lower(): v for k, v in attrs}
        prop = attr_map.get("property", "").lower()
        name = attr_map.get("name", "").lower()
        content = attr_map.get("content", "").strip()
        if not content:
            return

        if prop == "og:site_name" or name == "application-name":
            self.site_name = content

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


# ==========================
# HELPERS
# ==========================

def polite_sleep():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def normalize_duckduckgo_url(href):
    if not href:
        return None

    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        href = urljoin("https://duckduckgo.com", href)

    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)

    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def fetch_url(url):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            raw = resp.read(MAX_RESPONSE_BYTES)
            return raw.decode(charset, errors="replace")
    except (HTTPError, URLError, ValueError) as exc:
        logger.warning("Fetch failed: %s | %s", url, exc)
        return None


def parse_duckduckgo_results(html):
    parser = AnchorParser()
    parser.feed(html)
    results = []
    for link in parser.links:
        cls = link["attrs"].get("class", "")
        if "result__a" not in cls:
            continue
        title = " ".join((link.get("text") or "").split())
        url = normalize_duckduckgo_url(link.get("href"))
        if not url:
            continue
        results.append((title, url))
    return results


def is_blocked_domain(url):
    if not url:
        return True
    netloc = urlparse(url).netloc.lower()
    for blocked in BLOCKED_DOMAINS:
        if blocked in netloc:
            return True
    return False


def normalize_domain(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def base_website(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def clean_company_name(name):
    if not name:
        return None
    cleaned = " ".join(name.split())
    if not cleaned:
        return None

    separators = [" | ", " - ", " – ", " — ", " :: ", " · "]
    for sep in separators:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()

    cleaned = re.sub(r"\s+(Official\s+Site|Official\s+Website)$", "", cleaned, flags=re.I)
    cleaned = cleaned.strip()
    if cleaned.lower() in {"home", "welcome"}:
        return None
    return cleaned or None


def domain_to_company(domain):
    if not domain:
        return None
    core = domain.split(".")[0]
    core = core.replace("-", " ").replace("_", " ")
    return " ".join(word.capitalize() for word in core.split())


def extract_company_name(html, fallback_title, domain):
    name = None

    if html:
        parser = TitleParser()
        parser.feed(html)
        name = parser.site_name or parser.title
        name = clean_company_name(name)

    if not name:
        name = clean_company_name(fallback_title)

    if not name:
        name = domain_to_company(domain)

    return name


def load_existing_companies(output_file):
    seen_domains = set()
    if not os.path.exists(output_file):
        return seen_domains

    try:
        with open(output_file, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("website")
                domain = normalize_domain(url) if url else None
                if domain:
                    seen_domains.add(domain)
    except (OSError, csv.Error) as exc:
        logger.warning("Could not read existing output: %s", exc)
    return seen_domains


def build_search_terms():
    terms = []
    for query in BASE_QUERIES:
        terms.append(query)
        for country in COUNTRIES:
            terms.append(f"{query} {country}")
    return terms


def search_companies(writer, seen_domains):
    total = len(seen_domains)
    terms = build_search_terms()
    logger.info("Loaded %d existing companies", total)
    logger.info("Searching %d queries", len(terms))

    for term in terms:
        if total >= TARGET_COUNT:
            break
        for page in range(MAX_PAGES):
            if total >= TARGET_COUNT:
                break
            offset = page * 50
            search_url = (
                "https://duckduckgo.com/html/?q="
                + quote_plus(term)
                + f"&s={offset}"
            )
            logger.info("Query: %s | page %d", term, page + 1)
            html = fetch_url(search_url)
            polite_sleep()
            if not html:
                continue

            results = parse_duckduckgo_results(html)
            if not results:
                break

            for title, url in results:
                if total >= TARGET_COUNT:
                    break
                if is_blocked_domain(url):
                    continue

                domain = normalize_domain(url)
                if not domain or domain in seen_domains:
                    continue

                website = base_website(url)
                if not website:
                    continue

                page_html = fetch_url(url)
                name = extract_company_name(page_html, title, domain)
                if not name:
                    continue

                writer.writerow({
                    "company_name": name,
                    "website": website,
                })
                seen_domains.add(domain)
                total += 1
                logger.info("Saved (%d/%d): %s | %s", total, TARGET_COUNT, name, website)
                polite_sleep()

    return total


# ==========================
# MAIN
# ==========================

def main():
    seen_domains = load_existing_companies(OUTPUT_FILE)
    file_exists = os.path.exists(OUTPUT_FILE)

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["company_name", "website"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        total = search_companies(writer, seen_domains)

    logger.info("Finished with %d companies.", total)
    logger.info("Results saved to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
