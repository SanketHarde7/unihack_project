"""
Stage 2: Web Research Agent (Enhanced with Anti-Noise & Sourcing Compliance)
----------------------------------------------------------------------------
Root problem: we only have MPN + a 3-4 word cryptic description. To build an
accurate, rich product record we need real specs (dimensions, voltage, sound
level, capacity etc.) - these must come from the MANUFACTURER'S OWN OFFICIAL
SITE, per the hackathon sourcing rule (marketplaces/distributor sites are
explicitly disallowed).

Enterprise Upgrades:
  1. Multi-Query Strategy: 3 targeted queries (specs, install dimensions, spec-sheet PDF).
  2. Anti-Noise Filtering: Discards or deprioritizes troubleshooting articles, blogs,
     and registration pages to preserve token budget for real spec sheets.
  3. Spec-Priority Ranking: Surfaces URLs with /specifications, /spec-sheet, /products/, .pdf.
  4. Bot-Block Fallback: If direct requests encounters a 403/Forbidden, gracefully
     falls back to Tavily's pre-rendered content rather than losing the citation.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import requests
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

# Known official domains per brand. Sourcing-rule compliance strictly depends
# on domain locking.
BRAND_OFFICIAL_DOMAINS: dict[str, list[str]] = {
    "GE": ["geappliances.com"],
    "LG": ["lg.com"],
    "Kitchen Aid": ["kitchenaid.com"],
    "Frigidaire": ["frigidaire.com"],
    "Whirlpool": ["whirlpool.com"],
    "Speed Queen": ["speedqueen.com"],
    "Maytag": ["maytag.com"],
    "Samsung": ["samsung.com"],
    "Bosch": ["bosch-home.com"],
}

# URL noise patterns to filter out (troubleshooting, registration, generic blogs, press releases)
URL_BLACKLIST_PATTERNS = [
    "/support-articles/",
    "/article/",
    "/registration/",
    "/blog/",
    "/help/",
    "/community/",
    "/forum/",
    "/where-to-buy/",
    "/find-a-store/",
    "/contact-us",
    "/press-release/",
    "/press_release/",
    "/news/",
    "/promotions/",
]

# PDF marketing/noise signals to exclude
PDF_BLACKLIST_PATTERNS = [
    "press-release",
    "press_release",
    "news",
    "event",
    "promotion",
    "partnership",
    "campaign",
    "award",
    "announcement",
    "media",
    "corporate",
    "earnings",
    "story",
    "celebration",
]

# PDF technical/spec signals to prioritize
PDF_SPEC_PATTERNS = [
    "spec",
    "install",
    "manual",
    "guide",
    "datasheet",
    "data-sheet",
    "owner",
    "dimension",
    "tech-doc",
    "cut-sheet",
    "product-data",
]

# URL positive signals for specification data (HTML pages)
URL_PRIORITY_PATTERNS = [
    "/specifications",
    "/spec-sheet",
    "/specs",
    "/products/",
    "/dishwashers/",
    "/kitchen/",
    "/manuals",
    "/installation",
]


@dataclass
class ResearchResult:
    mpn: str
    brand: str
    status: str                 # "found" | "not_found" | "error"
    query: str = ""
    sources: list[dict] = field(default_factory=list)  # [{url, content, source_type, score, query}]
    raw_answer: str = ""


# ---------------------------------------------------------------------------
# HTML → plain text extractor (lightweight, no external deps)
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Strips HTML tags and extracts readable text content with whitespace collapse."""

    _SKIP_TAGS = frozenset({
        "script", "style", "noscript", "svg", "path", "meta", "link",
        "head", "iframe",
    })

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._pieces)
        return re.sub(r"\s+", " ", raw).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to clean plain text."""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return html
    return extractor.get_text()


# ---------------------------------------------------------------------------
# PDF text extractor (technical documents, spec sheets, installation manuals)
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_bytes: bytes, max_pages: int = 8) -> str:
    """Extract readable plaintext from PDF binary content using pdfminer."""
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(io.BytesIO(pdf_bytes), maxpages=max_pages)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception as exc:
        print(f"    [PDF extraction error]: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Full-page content fetcher (with bot-block fallback & PDF support)
# ---------------------------------------------------------------------------

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_MAX_PAGE_BYTES = 500_000


def _fetch_full_page(url: str, timeout: int = 10) -> tuple[str, str]:
    """Fetch full page content. Returns (plain_text, source_type)."""
    is_pdf_url = url.lower().split("?")[0].endswith(".pdf")
    try:
        resp = requests.get(
            url,
            headers=_FETCH_HEADERS,
            timeout=timeout,
            stream=not is_pdf_url,
            allow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").lower()

        # Handle PDF technical documents
        if is_pdf_url or "application/pdf" in content_type:
            pdf_text = _extract_pdf_text(resp.content, max_pages=8)
            resp.close()
            return pdf_text, "pdf"

        # Handle HTML web pages
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=True):
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8", errors="replace")
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_PAGE_BYTES:
                break
        resp.close()

        raw = "".join(chunks)

        if "html" in content_type or raw.strip().startswith("<"):
            return _html_to_text(raw), "html"

        return raw[:_MAX_PAGE_BYTES], "html"

    except Exception as exc:
        print(f"    [Fallback to snippet] {url} ({exc})")
        return "", "pdf" if is_pdf_url else "html"


# ---------------------------------------------------------------------------
# Multi-query & URL Ranking
# ---------------------------------------------------------------------------

def _build_queries(mpn: str, brand: str, product_type: str) -> list[str]:
    """Targeted search queries for deep spec discovery."""
    return [
        f"{brand} {mpn} specifications",
        f"{brand} {mpn} installation guide dimensions",
        f"{brand} {mpn} spec sheet PDF",
    ]


def _score_url(url: str) -> int:
    """Score a URL based on relevance to specs. Higher is better."""
    url_lower = url.lower()

    # Heavy penalty for noise/support/blog/press-release pages
    for blacklisted in URL_BLACKLIST_PATTERNS:
        if blacklisted in url_lower:
            return -10

    is_pdf = url_lower.split("?")[0].endswith(".pdf") or ".pdf" in url_lower

    if is_pdf:
        # Check PDF marketing/event noise signals -> heavy penalty
        for bad_signal in PDF_BLACKLIST_PATTERNS:
            if bad_signal in url_lower:
                return -15

        # Check PDF technical/spec signals -> strong boost
        for spec_signal in PDF_SPEC_PATTERNS:
            if spec_signal in url_lower:
                return 10

        # Generic PDF without spec or noise signals
        return 0

    # HTML page scoring
    score = 0
    for priority in URL_PRIORITY_PATTERNS:
        if priority in url_lower:
            score += 5

    return score


def research_product(mpn: str, brand: str, product_type: str = "dishwasher") -> ResearchResult:
    """Search the brand's official domain for this MPN's specs.

    Uses anti-noise ranking and multi-query search to prioritize official spec sheets and technical PDFs.
    """
    domains = BRAND_OFFICIAL_DOMAINS.get(brand)
    if not domains:
        return ResearchResult(
            mpn=mpn, brand=brand, status="error",
            raw_answer=f"No official domain registered for brand '{brand}'.",
        )

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return ResearchResult(
            mpn=mpn, brand=brand, status="error",
            raw_answer="TAVILY_API_KEY not set. Add it to your .env file.",
        )

    client = TavilyClient(api_key=api_key)
    queries = _build_queries(mpn, brand, product_type)

    seen_urls: set[str] = set()
    raw_sources: list[dict] = []
    raw_answers: list[str] = []

    for query in queries:
        try:
            response = client.search(
                query=query,
                include_domains=domains,
                search_depth="advanced",
                max_results=5,
            )
        except Exception as e:
            print(f"  WARN: Tavily query failed ({query!r}): {e}")
            continue

        answer = response.get("answer", "")
        if answer:
            raw_answers.append(answer)

        for r in response.get("results", []):
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            is_pdf = url.lower().split("?")[0].endswith(".pdf")
            raw_sources.append({
                "url": url,
                "content": r.get("content", ""),
                "score": _score_url(url),
                "query": query,
                "source_type": "pdf" if is_pdf else "html",
            })

    if not raw_sources:
        return ResearchResult(
            mpn=mpn, brand=brand, status="not_found",
            query=" | ".join(queries),
            raw_answer="No results on official domain across all queries - flag for manual review.",
        )

    # Sort sources: High-relevance spec pages first, deprioritize support/noise
    sorted_sources = sorted(raw_sources, key=lambda s: s["score"], reverse=True)

    # Filter out negative-scored URLs if we have at least 2 clean ones
    clean_sources = [s for s in sorted_sources if s["score"] >= 0]
    if len(clean_sources) >= 2:
        selected_sources = clean_sources
    else:
        selected_sources = sorted_sources

    # Fetch full page content for top 5 unique relevant URLs
    print(f"  Fetching full content for top {min(len(selected_sources), 5)} prioritized URLs...")
    for source in selected_sources[:5]:
        url = source["url"]
        full_text, src_type = _fetch_full_page(url)
        source["source_type"] = src_type
        if full_text and len(full_text) > len(source["content"]):
            source["content"] = full_text
            print(f"    OK [{src_type.upper()}]: {url} ({len(full_text)} chars)")
        else:
            print(f"    Kept snippet [{src_type.upper()}]: {url} ({len(source['content'])} chars)")

    combined_query = " | ".join(queries)
    combined_answer = "\n\n".join(raw_answers) if raw_answers else ""

    return ResearchResult(
        mpn=mpn, brand=brand, status="found",
        query=combined_query,
        sources=selected_sources,
        raw_answer=combined_answer,
    )


if __name__ == "__main__":
    for mpn, brand in [("PDSH4816AF", "Frigidaire"), ("WDTS7024RZ", "Whirlpool")]:
        result = research_product(mpn, brand)
        print(f"\n=== {mpn} ({brand}) ===")
        print("status:", result.status)
        if result.status == "found":
            print(f"sources: {len(result.sources)} unique URLs")
            for s in result.sources[:3]:
                print(f"  - [Score {s['score']}] {s['url']} ({len(s['content'])} chars)")
