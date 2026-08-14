"""
Stage 2: Web Research Agent
----------------------------
Root problem: we only have MPN + a 3-4 word cryptic description. To build an
accurate, rich product record we need real specs (dimensions, voltage, sound
level, capacity etc.) - these must come from the MANUFACTURER'S OWN OFFICIAL
SITE, per the hackathon sourcing rule (marketplaces/distributor sites are
explicitly disallowed).

Design decisions (with reasons, not guesses):
  - Tavily search is scoped with `include_domains` to the resolved brand's
    known official domain, so we never accidentally cite a reseller/marketplace.
  - MULTI-QUERY STRATEGY: We fire 2-3 targeted queries per MPN to maximise
    coverage (specs page, installation/PDF, spec-sheet PDF). Tavily snippets
    alone are too truncated for deep specs like voltage/amperage/dimensions.
  - FULL-PAGE FETCH: After Tavily gives us URLs, we fetch the full page HTML
    via requests and extract text, so the LLM sees the complete spec table
    instead of a 200-char snippet.
  - Each result is returned RAW with its source URL attached - Stage 3 (field
    generation) must cite what it used, and Stage 4 (validator) checks the
    domain is actually the official one. No silent trust.
  - If no official-domain result is found, we return an explicit "not_found"
    status rather than falling back to an unrestricted search.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import requests
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

# Known official domains per brand. Extend this as we verify more brands.
# Kept explicit (not auto-guessed) because sourcing-rule compliance depends
# on this being correct.
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


@dataclass
class ResearchResult:
    mpn: str
    brand: str
    status: str                 # "found" | "not_found" | "error"
    query: str = ""
    sources: list[dict] = field(default_factory=list)  # [{url, content}]
    raw_answer: str = ""


# ---------------------------------------------------------------------------
# HTML → plain text extractor (lightweight, no external deps)
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(HTMLParser):
    """Strips HTML tags and extracts readable text content."""

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
        # Collapse whitespace runs
        return re.sub(r"\s+", " ", raw).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, stripping tags and scripts."""
    extractor = _HTMLTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return html  # Fallback: return raw if parsing fails
    return extractor.get_text()


# ---------------------------------------------------------------------------
# Full-page content fetcher
# ---------------------------------------------------------------------------

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Max bytes to download per page (prevent OOM on huge PDFs)
_MAX_PAGE_BYTES = 500_000  # 500 KB of text is plenty for spec extraction


def _fetch_full_page(url: str, timeout: int = 10) -> str:
    """Fetch the full page content from a URL and return as plain text.

    For HTML pages, strips tags and returns readable text.
    For PDFs, returns what we can get (often the first chunk of text).
    Returns empty string on any failure (network, timeout, etc.).
    """
    try:
        resp = requests.get(
            url,
            headers=_FETCH_HEADERS,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")

        # Read up to _MAX_PAGE_BYTES
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

        # If it looks like HTML, strip tags
        if "html" in content_type.lower() or raw.strip().startswith("<"):
            return _html_to_text(raw)

        # Otherwise return as-is (could be plain text or PDF text)
        return raw[:_MAX_PAGE_BYTES]

    except Exception as exc:
        print(f"  WARN: Failed to fetch {url}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Multi-query search strategy
# ---------------------------------------------------------------------------

def _build_queries(mpn: str, brand: str, product_type: str) -> list[str]:
    """Build 3 targeted queries for maximum spec coverage."""
    return [
        # Query 1: Direct specs page
        f"{brand} {mpn} specifications",
        # Query 2: Installation/dimension data (often in install guides/PDFs)
        f"{brand} {mpn} installation guide dimensions",
        # Query 3: Spec sheet / data sheet (often has voltage/amperage/sound)
        f"{brand} {mpn} spec sheet product details",
    ]


def research_product(mpn: str, brand: str, product_type: str = "dishwasher") -> ResearchResult:
    """Search the brand's official domain for this MPN's specs.

    Uses multi-query strategy (3 targeted queries) and fetches full page
    content from discovered URLs for maximum data extraction.
    """

    domains = BRAND_OFFICIAL_DOMAINS.get(brand)
    if not domains:
        return ResearchResult(
            mpn=mpn, brand=brand, status="error",
            raw_answer=f"No official domain registered for brand '{brand}'. Add it to BRAND_OFFICIAL_DOMAINS first.",
        )

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return ResearchResult(
            mpn=mpn, brand=brand, status="error",
            raw_answer="TAVILY_API_KEY not set. Add it to your .env file.",
        )

    client = TavilyClient(api_key=api_key)
    queries = _build_queries(mpn, brand, product_type)

    # Deduplicate URLs across queries
    seen_urls: set[str] = set()
    all_sources: list[dict] = []
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
            all_sources.append({
                "url": url,
                "content": r.get("content", ""),
                "query": query,
            })

    if not all_sources:
        return ResearchResult(
            mpn=mpn, brand=brand, status="not_found",
            query=" | ".join(queries),
            raw_answer="No results on official domain across all queries - flag for manual review.",
        )

    # --- Full-page fetch for top URLs ---
    # Fetch full content for up to 5 unique URLs (most relevant first)
    print(f"  Fetching full page content for {min(len(all_sources), 5)} URLs...")
    for source in all_sources[:5]:
        url = source["url"]
        full_text = _fetch_full_page(url)
        if full_text and len(full_text) > len(source["content"]):
            # Replace Tavily snippet with full page text
            source["content"] = full_text
            print(f"    OK: {url} ({len(full_text)} chars)")
        else:
            print(f"    SKIP: {url} (snippet kept, {len(source['content'])} chars)")

    combined_query = " | ".join(queries)
    combined_answer = "\n\n".join(raw_answers) if raw_answers else ""

    return ResearchResult(
        mpn=mpn, brand=brand, status="found",
        query=combined_query,
        sources=all_sources,
        raw_answer=combined_answer,
    )


if __name__ == "__main__":
    # Quick smoke test on the two ground-truth items
    for mpn, brand in [("PDSH4816AF", "Frigidaire"), ("WDTS7024RZ", "Whirlpool")]:
        result = research_product(mpn, brand)
        print(f"\n=== {mpn} ({brand}) ===")
        print("status:", result.status)
        print("queries:", result.query)
        if result.status == "found":
            print(f"sources: {len(result.sources)} unique URLs")
            for s in result.sources[:3]:
                print(f"  - {s['url']} ({len(s['content'])} chars)")
        else:
            print("note:", result.raw_answer)
