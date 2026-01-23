#!/usr/bin/env python3
"""
Film crew candidate finder (names + emails).

Searches DuckDuckGo for role-focused queries (resume/portfolio/open-to-work),
visits each result, extracts candidate names and emails, and saves to CSV.

Use responsibly and respect target sites' terms and robots.txt.
"""

import csv
import logging
import os
import random
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ==========================
# CONFIG
# ==========================

OUTPUT_FILE = "film_role_candidate_contacts.csv"
LOG_FILE = "film_role_candidate_search.log"

TARGET_COUNT = 500
MAX_PAGES = 8
SLEEP_MIN = 1.5
SLEEP_MAX = 3.2

REQUEST_TIMEOUT = 25
MAX_RESPONSE_BYTES = 2_000_000  # 2 MB
MAX_RETRIES = 4
BACKOFF_BASE = 1.5
BACKOFF_MAX = 20
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

USE_COUNTRIES = True
COUNTRIES = [
    "USA", "UK", "India", "Canada", "Australia",
    "Germany", "France", "Italy", "Spain",
    "UAE", "South Africa", "New Zealand", "Ireland",
    "Netherlands", "Sweden", "Norway", "Denmark",
    "Mexico", "Brazil", "Japan", "South Korea",
]

SEARCH_TEMPLATES = [
    "{role} resume",
    "{role} cv",
    "{role} portfolio",
    "{role} demo reel",
    "{role} showreel",
    "{role} open to work",
    "{role} looking for work",
    "{role} available for hire",
    "{role} freelance",
    "{role} hire me",
    "{role} contact email",
]

SEEKING_KEYWORDS = [
    "resume",
    "cv",
    "curriculum vitae",
    "portfolio",
    "reel",
    "showreel",
    "demo reel",
    "open to work",
    "looking for work",
    "available for hire",
    "available for work",
    "hire me",
    "freelance",
    "seeking opportunities",
    "seeking work",
]

REQUIRE_SEEKING_KEYWORD = True

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
    "upwork.com",
    "fiverr.com",
    "freelancer.com",
    "peopleperhour.com",
    "mandy.com",
    "staffmeup.com",
    "productionhub.com",
    "backstage.com",
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

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ==========================
# ROLE CATEGORIES
# ==========================

ROLE_CATEGORIES = [
    {
        "id": "directing",
        "label": "Directing",
        "roles": [
            "Director",
            "1st Assistant Director",
            "2nd Assistant Director",
            "Script Supervisor",
        ],
    },
    {
        "id": "camera",
        "label": "Camera",
        "roles": [
            "Director of Photography (DP)",
            "Camera Operator",
            "1st AC (Focus Puller)",
            "2nd AC (Clapper Loader)",
            "DIT (Digital Imaging Technician)",
            "Steadicam Operator",
            "Drone Operator",
        ],
    },
    {
        "id": "lighting",
        "label": "Lighting & Grip",
        "roles": [
            "Gaffer",
            "Best Boy Electric",
            "Electrician",
            "Key Grip",
            "Best Boy Grip",
            "Grip",
            "Dolly Grip",
        ],
    },
    {
        "id": "sound",
        "label": "Sound",
        "roles": [
            "Sound Mixer",
            "Boom Operator",
            "Sound Utility",
            "Sound Designer",
            "Re-Recording Mixer",
            "Foley Artist",
        ],
    },
    {
        "id": "production",
        "label": "Production",
        "roles": [
            "Producer",
            "Line Producer",
            "Production Manager",
            "Production Coordinator",
            "Production Assistant (PA)",
            "Location Manager",
            "Location Scout",
        ],
    },
    {
        "id": "art",
        "label": "Art Department",
        "roles": [
            "Production Designer",
            "Art Director",
            "Set Decorator",
            "Set Dresser",
            "Props Master",
            "Prop Maker",
            "Scenic Artist",
        ],
    },
    {
        "id": "wardrobe",
        "label": "Wardrobe & Makeup",
        "roles": [
            "Costume Designer",
            "Wardrobe Supervisor",
            "Costume Assistant",
            "Makeup Artist",
            "Hair Stylist",
            "Special Effects Makeup",
        ],
    },
    {
        "id": "postproduction",
        "label": "Post-Production",
        "roles": [
            "Editor",
            "Assistant Editor",
            "Colorist",
            "VFX Supervisor",
            "VFX Artist",
            "Motion Graphics Designer",
            "Compositor",
        ],
    },
    {
        "id": "other",
        "label": "Other",
        "roles": [
            "Writer",
            "Casting Director",
            "Stunt Coordinator",
            "Choreographer",
            "BTS Photographer",
            "BTS Videographer",
            "Craft Services",
            "Transportation",
        ],
    },
]


# ==========================
# LOGGING
# ==========================

logger = logging.getLogger("film_role_candidate_search")
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


class PageMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.in_h1 = False
        self.title = ""
        self.h1s = []
        self._h1_buf = ""
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l == "title":
            self.in_title = True
            return
        if tag_l == "h1":
            self.in_h1 = True
            self._h1_buf = ""
            return
        if tag_l == "meta":
            attr_map = {k.lower(): v for k, v in attrs}
            prop = attr_map.get("property", "").lower()
            name = attr_map.get("name", "").lower()
            content = attr_map.get("content", "").strip()
            if content:
                if prop:
                    self.meta[prop] = content
                if name:
                    self.meta[name] = content

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        if tag_l == "title":
            self.in_title = False
        if tag_l == "h1":
            self.in_h1 = False
            cleaned = " ".join(self._h1_buf.split())
            if cleaned:
                self.h1s.append(cleaned)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_h1:
            self._h1_buf += data


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


def backoff_seconds(attempt):
    jitter = random.uniform(0, 0.6)
    return min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt) + jitter)


def fetch_url(url):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9",
        }
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                status = getattr(resp, "status", None)
                if status in RETRY_STATUS_CODES:
                    raise HTTPError(url, status, "Retryable status", resp.headers, None)

                content_type = resp.headers.get("Content-Type", "")
                if content_type and "text/html" not in content_type:
                    if "application/xhtml+xml" not in content_type:
                        logger.info("Skipping non-HTML content: %s | %s", url, content_type)
                        return None

                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                raw = resp.read(MAX_RESPONSE_BYTES)
                return raw.decode(charset, errors="replace")
        except HTTPError as exc:
            status = getattr(exc, "code", None)
            if status in RETRY_STATUS_CODES:
                last_exc = exc
                wait = backoff_seconds(attempt)
                logger.warning("HTTP %s for %s. Retrying in %.1fs", status, url, wait)
                time.sleep(wait)
                continue
            logger.warning("Fetch failed: %s | HTTP %s", url, status)
            return None
        except (
            URLError,
            ConnectionResetError,
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            OSError,
            ValueError,
        ) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = backoff_seconds(attempt)
                logger.warning("Fetch failed: %s | %s. Retrying in %.1fs", url, exc, wait)
                time.sleep(wait)
                continue
            logger.warning("Fetch failed: %s | %s", url, exc)
            return None

    if last_exc:
        logger.warning("Fetch gave up: %s | %s", url, last_exc)
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


def extract_emails(html):
    return sorted(set(EMAIL_RE.findall(html or "")))


def role_variants(role):
    role = role.strip()
    variants = set()
    variants.add(role.lower())

    if "(" in role and ")" in role:
        base = re.sub(r"\s*\([^)]*\)", "", role).strip()
        if base:
            variants.add(base.lower())
        inside = re.findall(r"\(([^)]*)\)", role)
        for token in inside:
            token = token.strip()
            if token:
                variants.add(token.lower())

    simplified = re.sub(r"[^a-zA-Z0-9\s]", " ", role).lower()
    simplified = " ".join(simplified.split())
    if simplified:
        variants.add(simplified)
    return [v for v in variants if v]


def clean_candidate_name(name):
    if not name:
        return None
    cleaned = " ".join(name.split())
    if not cleaned:
        return None

    separators = [" | ", " - ", " – ", " — ", " :: ", " · "]
    for sep in separators:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()

    cleaned = re.sub(r"\s+(resume|cv|portfolio|reel|showreel)$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+(official\s+site|homepage)$", "", cleaned, flags=re.I)
    cleaned = cleaned.strip()
    if cleaned.lower() in {"home", "welcome", "about", "contact"}:
        return None
    return cleaned or None


def looks_like_person_name(value):
    if not value:
        return False
    words = [w for w in re.split(r"\s+", value) if re.search(r"[A-Za-z]", w)]
    if len(words) < 2:
        return False
    for word in words[:4]:
        if word.isupper():
            continue
        if not word[0].isupper():
            return False
    return True


def name_from_email(email):
    if not email or "@" not in email:
        return None
    local = email.split("@", 1)[0]
    local = re.sub(r"\d+", " ", local)
    parts = re.split(r"[._\-]+", local)
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    return " ".join(p.capitalize() for p in parts)


def extract_candidate_name(html, fallback_title, email):
    if not html:
        name = clean_candidate_name(fallback_title)
        if name and looks_like_person_name(name):
            return name
        return name_from_email(email)

    parser = PageMetaParser()
    parser.feed(html)
    candidates = []

    for key in ("og:title", "twitter:title", "author", "profile:first_name", "profile:last_name"):
        value = parser.meta.get(key)
        if value:
            candidates.append(value)

    candidates.extend(parser.h1s)
    candidates.append(parser.title)
    candidates.append(fallback_title or "")

    for cand in candidates:
        cleaned = clean_candidate_name(cand)
        if cleaned and looks_like_person_name(cleaned):
            return cleaned

    return name_from_email(email)


def matches_seeking_intent(html_lower, title_lower, variants):
    if REQUIRE_SEEKING_KEYWORD:
        for keyword in SEEKING_KEYWORDS:
            if keyword in html_lower or keyword in title_lower:
                return True
        return False

    for keyword in SEEKING_KEYWORDS:
        if keyword in html_lower or keyword in title_lower:
            return True
    for variant in variants:
        if variant in html_lower or variant in title_lower:
            return True
    return False


def load_existing(output_file):
    seen_emails = set()
    seen_urls = set()
    if not os.path.exists(output_file):
        return seen_emails, seen_urls

    try:
        with open(output_file, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = (row.get("email") or "").strip().lower()
                url = (row.get("source_url") or "").strip()
                if email:
                    seen_emails.add(email)
                if url:
                    seen_urls.add(url)
    except (OSError, csv.Error) as exc:
        logger.warning("Could not read existing output: %s", exc)
    return seen_emails, seen_urls


def build_search_terms(role):
    terms = []
    for template in SEARCH_TEMPLATES:
        base = template.format(role=role)
        terms.append(base)
        if USE_COUNTRIES:
            for country in COUNTRIES:
                terms.append(f"{base} {country}")
    return terms


def search_role(writer, role, category, seen_emails, seen_urls, total_count):
    terms = build_search_terms(role)
    variants = role_variants(role)

    for term in terms:
        if total_count >= TARGET_COUNT:
            break
        for page in range(MAX_PAGES):
            if total_count >= TARGET_COUNT:
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
                if total_count >= TARGET_COUNT:
                    break
                if not url or url in seen_urls:
                    continue
                if is_blocked_domain(url):
                    continue
                seen_urls.add(url)

                page_html = fetch_url(url)
                polite_sleep()
                if not page_html:
                    continue

                emails = extract_emails(page_html)
                if not emails:
                    continue

                title_lower = (title or "").lower()
                html_lower = page_html.lower()
                if not matches_seeking_intent(html_lower, title_lower, variants):
                    continue

                for email in emails:
                    email_l = email.lower()
                    if email_l in seen_emails:
                        continue

                    name = extract_candidate_name(page_html, title, email)
                    if not name:
                        continue

                    writer.writerow({
                        "name": name,
                        "email": email,
                        "role": role,
                        "category": category,
                        "source_url": url,
                        "page_title": title,
                    })
                    seen_emails.add(email_l)
                    total_count += 1
                    logger.info("Saved (%d/%d): %s | %s", total_count, TARGET_COUNT, name, email)
                    if total_count >= TARGET_COUNT:
                        break

    return total_count


# ==========================
# MAIN
# ==========================

def main():
    seen_emails, seen_urls = load_existing(OUTPUT_FILE)
    file_exists = os.path.exists(OUTPUT_FILE)

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["name", "email", "role", "category", "source_url", "page_title"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        total = len(seen_emails)
        for category in ROLE_CATEGORIES:
            label = category["label"]
            for role in category["roles"]:
                logger.info("Searching role: %s (%s)", role, label)
                total = search_role(
                    writer, role, label, seen_emails, seen_urls, total
                )
                if total >= TARGET_COUNT:
                    break
            if total >= TARGET_COUNT:
                break

    logger.info("Finished with %d candidates.", total)
    logger.info("Results saved to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
