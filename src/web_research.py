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
  - Each result is returned RAW with its source URL attached - Stage 3 (field
    generation) must cite what it used, and Stage 4 (validator) checks the
    domain is actually the official one. No silent trust.
  - If no official-domain result is found, we return an explicit "not_found"
    status rather than falling back to an unrestricted search - this protects
    the sourcing rule at the cost of coverage, which is the correct tradeoff
    for judged accuracy.
"""

import os
from dataclasses import dataclass, field

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


def research_product(mpn: str, brand: str, product_type: str = "dishwasher") -> ResearchResult:
    """Search the brand's official domain for this MPN's specs."""

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
    query = f"{brand} {product_type} {mpn} specifications dimensions"

    try:
        response = client.search(
            query=query,
            include_domains=domains,
            search_depth="advanced",
            max_results=5,
        )
    except Exception as e:
        return ResearchResult(mpn=mpn, brand=brand, status="error", query=query, raw_answer=str(e))

    results = response.get("results", [])
    if not results:
        return ResearchResult(
            mpn=mpn, brand=brand, status="not_found", query=query,
            raw_answer="No results on official domain - flag this MPN for manual review.",
        )

    sources = [{"url": r["url"], "content": r.get("content", "")} for r in results]
    return ResearchResult(
        mpn=mpn, brand=brand, status="found", query=query,
        sources=sources, raw_answer=response.get("answer", ""),
    )


if __name__ == "__main__":
    # Quick smoke test on the two ground-truth items
    for mpn, brand in [("PDSH4816AF", "Frigidaire"), ("WDTS7024RZ", "Whirlpool")]:
        result = research_product(mpn, brand)
        print(f"\n=== {mpn} ({brand}) ===")
        print("status:", result.status)
        print("query:", result.query)
        if result.status == "found":
            for s in result.sources[:2]:
                print(" -", s["url"])
        else:
            print("note:", result.raw_answer)
