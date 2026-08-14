from __future__ import annotations

"""
Stage 1: Brand Resolver
------------------------
Root problem: Part_Manuf field in raw data is the DISTRIBUTOR (e.g. "Appliance
Dealers Cooperative"), NOT the real manufacturer/brand. We confirmed this from
ground-truth: PDSH4816AF -> real MANUFACTURER_NAME = "Rheem Manufacturing",
BRAND_NAME = "Frigidaire(R)". Distributor name never appears in output.

This module resolves the REAL brand using two signals, in priority order:
  1. Explicit brand keyword found in Part_Desc text (highest confidence)
  2. MPN prefix pattern (learned from known examples)

Anything unresolved is flagged, never guessed silently.
"""

import re
from dataclasses import dataclass


# Known brand keywords that may appear directly in Part_Desc.
# Matched case-insensitively as whole words to avoid partial-match bugs
# (e.g. "GE" must not match inside "RANGE" or "STORAGE").
BRAND_KEYWORDS = {
    "GE": r"\bGE\b",
    "LG": r"\bLG\b",
    "Kitchen Aid": r"\bKITCHEN\s*AID\b",
    "Frigidaire": r"\bFRIGIDAIRE\b",
    "Whirlpool": r"\bWHIRLPOOL\b",
    "Speed Queen": r"\bSPEED\s*QUEEN\b|\bSQ\b",
    "Maytag": r"\bMAYTAG\b",
    "Samsung": r"\bSAMSUNG\b",
    "Bosch": r"\bBOSCH\b",
}

# MPN prefix -> brand, learned from ground-truth rows + APPDE cluster patterns.
# ONLY includes prefixes we have direct evidence for. Do not extrapolate
# blindly - unmatched prefixes must fall through to "unresolved".
MPN_PREFIX_MAP = {
    "PDSH": "Frigidaire",   # confirmed from ground truth row 1
    "WDTS": "Whirlpool",    # confirmed from ground truth row 2
    "KDFM": "Kitchen Aid",  # KD prefix family, needs web confirmation
    "KDTS": "Kitchen Aid",
    "KDPS": "Kitchen Aid",
    "PDT":  "GE",           # explicit "Ge" in desc, prefix noted for reuse
    "PDD":  "GE",
    "LDPH": "LG",
}


@dataclass
class BrandResolution:
    mpn: str
    brand: str | None
    confidence: str      # "high" | "medium" | "needs_review"
    source: str           # how it was resolved, for audit/debug


def resolve_brand(mfg_part_num: str, part_desc: str) -> BrandResolution:
    """Resolve the real brand for one row. Never silently guesses."""

    # Signal 1: explicit brand keyword in description text (highest confidence)
    for brand, pattern in BRAND_KEYWORDS.items():
        if re.search(pattern, part_desc, re.IGNORECASE):
            return BrandResolution(
                mpn=mfg_part_num,
                brand=brand,
                confidence="high",
                source=f"text_match:'{pattern}'",
            )

    # Signal 2: MPN prefix pattern (medium confidence - needs web verification
    # in Stage 2 before being trusted for final output)
    for prefix, brand in MPN_PREFIX_MAP.items():
        if mfg_part_num.upper().startswith(prefix):
            return BrandResolution(
                mpn=mfg_part_num,
                brand=brand,
                confidence="medium",
                source=f"mpn_prefix:'{prefix}'",
            )

    # No signal found - flag for manual/LLM-assisted review, do NOT invent
    return BrandResolution(
        mpn=mfg_part_num,
        brand=None,
        confidence="needs_review",
        source="no_match",
    )


if __name__ == "__main__":
    import csv

    with open("/home/claude/unihack/data/Unihack__Sample_Dataset_-_Input.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if "APPDE" in r["Part_Manuf"] and "DISHWASHER" in r["Part_Desc"].upper()]

    print(f"Testing brand resolver on {len(rows)} dishwashers:\n")
    for r in rows:
        res = resolve_brand(r["Mfg_Part_Num"], r["Part_Desc"])
        print(f"{res.mpn:15s} | brand={res.brand!s:15s} | confidence={res.confidence:12s} | {res.source}")
