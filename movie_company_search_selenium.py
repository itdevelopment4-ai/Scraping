#!/usr/bin/env python3
"""
Selenium movie/film company finder.

Opens Chrome, searches DuckDuckGo for movie/film/documentary production companies,
filters list/directory pages, and saves company name + website to CSV.

Designed to run overnight and stop at TARGET_COUNT (default: 2000).
"""

import csv
import logging
import os
import random
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# ==========================
# CONFIG
# ==========================

OUTPUT_FILE = "movie_production_companies_selenium.csv"
LOG_FILE = "movie_company_search_selenium.log"

TARGET_COUNT = 2000
MAX_PAGES = 12
SLEEP_MIN = 1.8
SLEEP_MAX = 3.8
PAGE_LOAD_TIMEOUT = 25
MAX_RETRIES = 3

HEADLESS = False

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
    "documentary production company",
    "documentary film production company",
    "documentary production studio",
    "documentary film studio",
    "movie sound production company",
    "film sound production company",
    "movie sound studio",
    "film sound studio",
    "post production sound company",
    "film post production company",
    "movie post production company",
    "audio post production for film",
    "post production studio",
    "visual effects studio",
    "vfx studio",
    "animation studio",
    "film post production studio",
    "audio post production studio",
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
    "justdial.com",
    "sulekha.com",
    "grotal.com",
    "indiamart.com",
    "clutch.co",
    "designrush.com",
    "goodfirms.co",
    "sortlist.com",
    "upcity.com",
    "themanifest.com",
    "agencyspotter.com",
    "yelp.com",
    "yellowpages.com",
    "manta.com",
    "cylex.com",
    "kompass.com",
    "dnb.com",
    "hoovers.com",
    "owler.com",
    "zoominfo.com",
    "crunchbase.com",
    "pitchbook.com",
    "opencorporates.com",
    "productionhub.com",
    "backstage.com",
    "staffmeup.com",
    "mandy.com",
    "behance.net",
    "vimeo.com",
    "vitrina.ai",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "simplyhired.com",
    "careerbuilder.com",
]

TITLE_BLOCK_PATTERNS = [
    re.compile(r"\btop\s+\d+\b", re.I),
    re.compile(r"\bbest\b.*\b(companies|agencies|studios)\b", re.I),
    re.compile(r"\blist of\b", re.I),
    re.compile(r"\b(companies|studios|agencies)\s+in\b", re.I),
    re.compile(r"\bproduction\s+companies\b", re.I),
    re.compile(r"\bvideo\s+production\s+companies\b", re.I),
    re.compile(r"\bfilm\s+production\s+companies\b", re.I),
    re.compile(r"\bdirectory\b", re.I),
    re.compile(r"\breviews?\b", re.I),
    re.compile(r"\bratings?\b", re.I),
    re.compile(r"\bnear me\b", re.I),
    re.compile(r"\bjobs?\b", re.I),
    re.compile(r"\bcareers?\b", re.I),
    re.compile(r"\bacademy\b|\bschool\b|\bcollege\b|\btraining\b", re.I),
    re.compile(r"\bblog\b|\bnews\b|\barticle\b", re.I),
]

URL_BLOCK_RE = re.compile(
    r"/(top|best|list|directory|reviews?|ratings?|rank|companies-in|"
    r"production-companies|video-production-companies|film-production-companies|"
    r"blog|news|article|jobs?|careers?|academy|course|training|school|pricing|"
    r"cost|quote|compare)",
    re.I,
)

COMPANY_SIGNAL_KEYWORDS = [
    "studio",
    "studios",
    "production",
    "films",
    "film",
    "movie",
    "movies",
    "pictures",
    "media",
    "entertainment",
    "documentary",
    "post-production",
    "post production",
    "sound",
    "audio",
    "vfx",
    "visual effects",
    "animation",
    "cinema",
]

CONTACT_SIGNAL_KEYWORDS = [
    "contact",
    "about",
    "our team",
    "team",
    "services",
    "projects",
    "our work",
    "clients",
]

HARD_NEGATIVE_PAGE_KEYWORDS = [
    "list of",
    "companies in",
    "directory",
    "top ",
    "best ",
    "reviews",
    "ratings",
    "compare",
    "comparison",
    "near me",
]

SOFT_NEGATIVE_PAGE_KEYWORDS = [
    "jobs",
    "careers",
    "open positions",
    "academy",
    "course",
    "school",
    "college",
    "training",
    "blog",
    "news",
    "article",
    "digital marketing",
    "seo",
]

GENERIC_NAME_PATTERNS = [
    re.compile(r"\btop\b", re.I),
    re.compile(r"\bbest\b", re.I),
    re.compile(r"\blist\b", re.I),
    re.compile(r"\bcompanies\b", re.I),
    re.compile(r"\bproduction\s+companies\b", re.I),
    re.compile(r"\bvideo\s+production\s+companies\b", re.I),
    re.compile(r"\bfilm\s+production\s+companies\b", re.I),
    re.compile(r"\bproduction\s+houses\b", re.I),
    re.compile(r"\bjobs?\b", re.I),
    re.compile(r"\bcareers?\b", re.I),
    re.compile(r"\bnear me\b", re.I),
    re.compile(r"\bdirectory\b", re.I),
    re.compile(r"\breviews?\b", re.I),
    re.compile(r"\bratings?\b", re.I),
    re.compile(r"\bmarketing\b", re.I),
    re.compile(r"\bagency\b", re.I),
    re.compile(r"\bacademy\b|\bschool\b|\bcollege\b", re.I),
]


# ==========================
# LOGGING
# ==========================

logger = logging.getLogger("movie_company_search_selenium")
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


def is_list_or_directory_title(title):
    title = title or ""
    for pattern in TITLE_BLOCK_PATTERNS:
        if pattern.search(title):
            return True
    return False


def is_bad_url_path(url):
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    return bool(URL_BLOCK_RE.search(path) or URL_BLOCK_RE.search(query))


def page_looks_like_company(html):
    html_lower = html.lower()
    if any(key in html_lower for key in HARD_NEGATIVE_PAGE_KEYWORDS):
        return False

    contact_hit = (
        "mailto:" in html_lower
        or "tel:" in html_lower
        or any(key in html_lower for key in CONTACT_SIGNAL_KEYWORDS)
    )
    company_hits = sum(1 for key in COMPANY_SIGNAL_KEYWORDS if key in html_lower)

    if contact_hit:
        return True

    if company_hits >= 2 and not any(key in html_lower for key in SOFT_NEGATIVE_PAGE_KEYWORDS):
        return True

    return False


def is_generic_name(name):
    if not name:
        return True
    if len(name) > 80:
        return True
    lowered = name.lower()
    for pattern in GENERIC_NAME_PATTERNS:
        if pattern.search(lowered):
            return True
    if " in " in lowered and "companies" in lowered:
        return True
    return False


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


def build_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def safe_get(driver, url):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            driver.get(url)
            return True
        except TimeoutException as exc:
            last_exc = exc
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            logger.warning("Timeout loading %s (attempt %d)", url, attempt + 1)
        except WebDriverException as exc:
            last_exc = exc
            logger.warning("WebDriver error on %s: %s", url, exc)
        time.sleep(1 + attempt)
    logger.warning("Failed to load %s after retries: %s", url, last_exc)
    return False


def parse_search_results(driver):
    results = []
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.result__a"))
        )
    except Exception:
        return results

    for link in driver.find_elements(By.CSS_SELECTOR, "a.result__a"):
        title = link.text.strip()
        href = link.get_attribute("href")
        url = normalize_duckduckgo_url(href)
        if not url:
            continue
        results.append((title, url))
    return results


def search_companies(driver, writer, seen_domains):
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
            if not safe_get(driver, search_url):
                continue
            polite_sleep()

            results = parse_search_results(driver)
            if not results:
                break

            for title, url in results:
                if total >= TARGET_COUNT:
                    break
                if is_blocked_domain(url):
                    continue
                if is_list_or_directory_title(title):
                    continue
                if is_bad_url_path(url):
                    continue

                domain = normalize_domain(url)
                if not domain or domain in seen_domains:
                    continue

                website = base_website(url)
                if not website:
                    continue

                if not safe_get(driver, url):
                    continue
                polite_sleep()

                page_html = driver.page_source or ""
                if not page_html:
                    continue

                if not page_looks_like_company(page_html):
                    continue

                name = extract_company_name(page_html, title, domain)
                if not name or is_generic_name(name):
                    continue

                writer.writerow({
                    "company_name": name,
                    "website": website,
                })
                writer_fh = writer.writerows.__self__
                writer_fh.flush()

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

    driver = build_driver()
    try:
        with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["company_name", "website"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            total = search_companies(driver, writer, seen_domains)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    logger.info("Finished with %d companies.", total)
    logger.info("Results saved to: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
