#!/usr/bin/env python3
"""
Overnight-friendly film role outreach scraper.

What it does:
- Queries DuckDuckGo HTML results for film role + country searches.
- Filters results by relevance and blocked domains.
- Visits each result page to extract emails/phones.
- Optionally checks a couple of "contact/about" pages per site.
- Writes results to a CSV you can open in Excel.

Notes:
- Be respectful of target sites' terms and robots.txt.
- Tune sleep settings to reduce load and avoid blocks.
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

OUTPUT_FILE = "film_role_contact_results.csv"
LOG_FILE = "film_role_contact_scraper.log"

MAX_PAGES = 6
SLEEP_MIN = 2.5
SLEEP_MAX = 5.5

REQUEST_TIMEOUT = 25
MAX_RESPONSE_BYTES = 2_000_000  # 2 MB
MAX_CONTACT_PAGES_PER_SITE = 2

COUNTRIES = [
    "USA", "UK", "India", "Canada", "Australia",
    "Germany", "France", "Italy", "Spain",
    "UAE", "South Africa",
]

SEARCH_TEMPLATES = [
    "{role} film job",
    "{role} film hiring",
    "{role} film crew needed",
    "{role} film production vacancy",
    "{role} freelance film work",
]

RELEVANT_KEYWORDS = [
    "job", "hiring", "vacancy", "opening",
    "apply", "required", "wanted", "crew",
]

BLOCKED_DOMAINS = [
    "wikipedia.org",
    "imdb.com",
    "britannica.com",
    "youtube.com",
]

CONTACT_KEYWORDS = [
    "contact", "about", "team", "staff", "company",
    "production", "jobs", "careers",
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
# ROLE CATEGORIES
# ==========================

ROLE_CATEGORIES = {
    "Directing": [
        "Director",
        "1st Assistant Director",
        "2nd Assistant Director",
        "Script Supervisor",
    ],
    "Camera": [
        "Director of Photography",
        "Camera Operator",
        "1st AC",
        "2nd AC",
        "DIT",
        "Steadicam Operator",
        "Drone Operator",
    ],
    "Lighting & Grip": [
        "Gaffer",
        "Best Boy Electric",
        "Electrician",
        "Key Grip",
        "Best Boy Grip",
        "Grip",
        "Dolly Grip",
    ],
    "Sound": [
        "Sound Mixer",
        "Boom Operator",
        "Sound Utility",
        "Sound Designer",
        "Re-Recording Mixer",
        "Foley Artist",
    ],
    "Production": [
        "Producer",
        "Line Producer",
        "Production Manager",
        "Production Coordinator",
        "Production Assistant",
        "Location Manager",
        "Location Scout",
    ],
    "Art Department": [
        "Production Designer",
        "Art Director",
        "Set Decorator",
        "Set Dresser",
        "Props Master",
        "Prop Maker",
        "Scenic Artist",
    ],
    "Wardrobe & Makeup": [
        "Costume Designer",
        "Wardrobe Supervisor",
        "Costume Assistant",
        "Makeup Artist",
        "Hair Stylist",
        "Special Effects Makeup",
    ],
    "Post-Production": [
        "Editor",
        "Assistant Editor",
        "Colorist",
        "VFX Supervisor",
        "VFX Artist",
        "Motion Graphics Designer",
        "Compositor",
    ],
    "Other": [
        "Writer",
        "Casting Director",
        "Stunt Coordinator",
        "Choreographer",
        "BTS Photographer",
        "BTS Videographer",
        "Craft Services",
        "Transportation",
    ],
}


# ==========================
# LOGGING
# ==========================

logger = logging.getLogger("film_role_scraper")
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


# ==========================
# HELPERS
# ==========================

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-.]?)?"
    r"(?:\(?\d{2,4}\)?[\s\-.]?)?"
    r"\d{3,4}[\s\-.]?\d{3,4}"
    r"(?:[\s\-.]?\d{2,4})?"
)


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


def parse_links(html):
    parser = AnchorParser()
    parser.feed(html)
    return parser.links


def parse_duckduckgo_results(html):
    links = parse_links(html)
    results = []
    for link in links:
        cls = link["attrs"].get("class", "")
        if "result__a" not in cls:
            continue
        title = " ".join((link.get("text") or "").split())
        url = normalize_duckduckgo_url(link.get("href"))
        if not url:
            continue
        results.append((title, url))
    return results


def is_valid_result(title, url):
    title_l = (title or "").lower()
    url_l = (url or "").lower()

    if any(bad in url_l for bad in BLOCKED_DOMAINS):
        return False
    if not any(k in title_l for k in RELEVANT_KEYWORDS):
        return False
    return True


def normalize_phone(candidate):
    if not candidate:
        return None
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 7:
        return None
    return candidate.strip()


def extract_contacts(html):
    emails = set(EMAIL_RE.findall(html))
    phones = set()

    for match in PHONE_RE.findall(html):
        normalized = normalize_phone(match)
        if normalized:
            phones.add(normalized)

    parser = AnchorParser()
    parser.feed(html)
    for link in parser.links:
        href = (link.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0].strip()
            if addr:
                emails.add(addr)
        if href.lower().startswith("tel:"):
            number = href.split(":", 1)[1].split("?", 1)[0].strip()
            normalized = normalize_phone(number)
            if normalized:
                phones.add(normalized)

    return sorted(emails), sorted(phones)


def find_contact_links(base_url, html):
    parser = AnchorParser()
    parser.feed(html)
    domain = urlparse(base_url).netloc
    candidates = []

    for link in parser.links:
        href = link.get("href") or ""
        text = (link.get("text") or "").strip().lower()
        haystack = (text + " " + href.lower()).strip()
        if not any(keyword in haystack for keyword in CONTACT_KEYWORDS):
            continue

        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc and parsed.netloc != domain:
            continue
        candidates.append(url)

    seen = set()
    unique = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
        if len(unique) >= MAX_CONTACT_PAGES_PER_SITE:
            break
    return unique


def load_seen_urls(output_file):
    seen = set()
    if not os.path.exists(output_file):
        return seen
    try:
        with open(output_file, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("url")
                if url:
                    seen.add(url)
    except (OSError, csv.Error) as exc:
        logger.warning("Could not read existing output: %s", exc)
    return seen


def gather_contact_details(url, seen_contact_urls):
    page_html = fetch_url(url)
    if not page_html:
        return [], [], []

    emails, phones = extract_contacts(page_html)
    contact_pages = []

    for contact_url in find_contact_links(url, page_html):
        if contact_url in seen_contact_urls:
            continue
        seen_contact_urls.add(contact_url)
        contact_html = fetch_url(contact_url)
        if not contact_html:
            continue
        extra_emails, extra_phones = extract_contacts(contact_html)
        emails.extend(extra_emails)
        phones.extend(extra_phones)
        contact_pages.append(contact_url)
        polite_sleep()

    emails = sorted(set(emails))
    phones = sorted(set(phones))
    return emails, phones, contact_pages


def search_role(writer, role, category, seen_urls, seen_contact_urls):
    logger.info("Searching role: %s (%s)", role, category)

    for country in COUNTRIES:
        for template in SEARCH_TEMPLATES:
            query = template.format(role=role) + f" {country}"

            for page in range(MAX_PAGES):
                offset = page * 50
                search_url = (
                    "https://duckduckgo.com/html/?q="
                    + quote_plus(query)
                    + f"&s={offset}"
                )

                logger.info("Query: %s | page %d", query, page + 1)
                html = fetch_url(search_url)
                polite_sleep()
                if not html:
                    continue

                results = parse_duckduckgo_results(html)
                if not results:
                    break

                for title, link in results:
                    if not link or link in seen_urls:
                        continue
                    if not is_valid_result(title, link):
                        continue

                    seen_urls.add(link)
                    emails, phones, contact_pages = gather_contact_details(
                        link, seen_contact_urls
                    )
                    writer.writerow({
                        "role": role,
                        "category": category,
                        "country": country,
                        "title": title,
                        "url": link,
                        "emails": ";".join(emails),
                        "phones": ";".join(phones),
                        "contact_pages": ";".join(contact_pages),
                    })
                    logger.info("Saved result: %s", link)
                    polite_sleep()


# ==========================
# MAIN
# ==========================

def main():
    seen_urls = load_seen_urls(OUTPUT_FILE)
    seen_contact_urls = set()

    file_exists = os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = [
            "role", "category", "country", "title", "url",
            "emails", "phones", "contact_pages",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for category, roles in ROLE_CATEGORIES.items():
            for role in roles:
                search_role(writer, role, category, seen_urls, seen_contact_urls)

    logger.info("Done. Results saved to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
