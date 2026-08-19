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


# Master brand keywords across Appliances, Plumbing, HVAC, Electrical, Tools, and Industrial Equipment.
# Matched case-insensitively as whole words to avoid partial-match bugs.
BRAND_KEYWORDS = {
    # — Major Appliances & Consumer Electronics —
    "GE": r"\bGE\b|\bGENERAL\s*ELECTRIC\b",
    "LG": r"\bLG\b",
    "Kitchen Aid": r"\bKITCHEN\s*AID\b|\bKITCHENAID\b",
    "Frigidaire": r"\bFRIGIDAIRE\b",
    "Whirlpool": r"\bWHIRLPOOL\b",
    "Speed Queen": r"\bSPEED\s*QUEEN\b|\bSQ\b",
    "Maytag": r"\bMAYTAG\b",
    "Samsung": r"\bSAMSUNG\b",
    "Bosch": r"\bBOSCH\b",
    "Electrolux": r"\bELECTROLUX\b",
    "Haier": r"\bHAIER\b",
    "Miele": r"\bMIELE\b",
    "JennAir": r"\bJENN\s*AIR\b|\bJENNAIR\b",
    "Amana": r"\bAMANA\b",
    "Viking": r"\bVIKING\b",
    "Thermador": r"\bTHERMADOR\b",
    "Sub-Zero": r"\bSUB\s*ZERO\b|\bSUB-ZERO\b",
    "Wolf": r"\bWOLF\b",
    "Dacor": r"\bDACOR\b",
    "Fisher & Paykel": r"\bFISHER\s*&\s*PAYKEL\b|\bFISHER\s*PAYKEL\b",
    "Sharp": r"\bSHARP\b",
    "Panasonic": r"\bPANASONIC\b",
    "Danby": r"\bDANBY\b",
    "Avanti": r"\bAVANTI\b",

    # — Plumbing Fixtures, Faucets & Valves (FAUCETS_LOV) —
    "Kohler": r"\bKOHLER\b",
    "Moen": r"\bMOEN\b",
    "Delta": r"\bDELTA\b|\bDELTA\s*FAUCET\b",
    "American Standard": r"\bAMERICAN\s*STANDARD\b",
    "Pfister": r"\bPFISTER\b|\bPRICE\s*PFISTER\b",
    "Grohe": r"\bGROHE\b",
    "Hansgrohe": r"\bHANSGROHE\b",
    "TOTO": r"\bTOTO\b",
    "Sloan": r"\bSLOAN\b",
    "Zurn": r"\bZURN\b",
    "Watts": r"\bWATTS\b",
    "Elkay": r"\bELKAY\b",
    "Gerber": r"\bGERBER\b",
    "Symmons": r"\bSYMMONS\b",
    "Chicago Faucets": r"\bCHICAGO\s*FAUCETS\b",
    "Speakman": r"\bSPEAKMAN\b",
    "InSinkErator": r"\bINSINKERATOR\b|\bISE\b",
    "Mansfield": r"\bMANSFIELD\b",

    # — Pipes, Valves & Fittings (Fittings_LOV) —
    "Apollo Valves": r"\bAPOLLO\b|\bCONBRACO\b",
    "Nibco": r"\bNIBCO\b",
    "Charlotte Pipe": r"\bCHARLOTTE\b|\bCHARLOTTE\s*PIPE\b",
    "SharkBite": r"\bSHARKBITE\b",
    "Mueller": r"\bMUELLER\b|\bMUELLER\s*INDUSTRIES\b",
    "Vieqa": r"\bVIEGA\b",
    "Anvil": r"\bANVIL\b|\bANVIL\s*INTERNATIONAL\b",
    "Victaulic": r"\bVICTAULIC\b",
    "Dixon Valve": r"\bDIXON\b|\bDIXON\s*VALVE\b",
    "Parker": r"\bPARKER\b|\bPARKER\s*HANNIFIN\b",
    "Spears": r"\bSPEARS\b",
    "IPEX": r"\bIPEX\b",
    "Legend Valve": r"\bLEGEND\b|\bLEGEND\s*VALVE\b",

    # — HVAC & Water Heating —
    "Carrier": r"\bCARRIER\b",
    "Trane": r"\bTRANE\b",
    "Rheem": r"\bRHEEM\b",
    "Ruud": r"\bRUUD\b",
    "Lennox": r"\bLENNOX\b",
    "Goodman": r"\bGOODMAN\b",
    "York": r"\bYORK\b",
    "Daikin": r"\bDAIKIN\b",
    "Mitsubishi Electric": r"\bMITSUBISHI\b|\bMITSUBISHI\s*ELECTRIC\b",
    "A.O. Smith": r"\bA\.?O\.?\s*SMITH\b|\bAO\s*SMITH\b",
    "Bradford White": r"\bBRADFORD\s*WHITE\b",
    "Rinnai": r"\bRINNAI\b",
    "Navien": r"\bNAVIEN\b",
    "Honeywell": r"\bHONEYWELL\b|\bRESIDEO\b",
    "White-Rodgers": r"\bWHITE\s*RODGERS\b|\bEMERSON\b",
    "Copeland": r"\bCOPELAND\b",
    "Broan-NuTone": r"\bBROAN\b|\bNUTONE\b",

    # — Electrical, Power Distribution & Automation —
    "Square D": r"\bSQUARE\s*D\b|\bSCHNEIDER\b|\bSCHNEIDER\s*ELECTRIC\b",
    "Eaton": r"\bEATON\b|\bCUTLER\s*HAMMER\b",
    "Siemens": r"\bSIEMENS\b",
    "Leviton": r"\bLEVITON\b",
    "Hubbell": r"\bHUBBELL\b",
    "Legrand": r"\bLEGRAND\b|\bPASS\s*&\s*SEYMOUR\b",
    "Lutron": r"\bLUTRON\b",
    "ABB": r"\bABB\b",
    "Rockwell Automation": r"\bALLEN\s*BRADLEY\b|\bROCKWELL\b",
    "Southwire": r"\bSOUTHWIRE\b",
    "Ideal Industries": r"\bIDEAL\b|\bIDEAL\s*INDUSTRIES\b",

    # — Tools, Hardware & Industrial Supplies —
    "Milwaukee": r"\bMILWAUKEE\b|\bMILWAUKEE\s*TOOL\b",
    "DeWalt": r"\bDEWALT\b",
    "Makita": r"\bMAKITA\b",
    "Klein Tools": r"\bKLEIN\b|\bKLEIN\s*TOOLS\b",
    "Fluke": r"\bFLUKE\b",
    "RIDGID": r"\bRIDGID\b",
    "3M": r"\b3M\b",
    "Stanley": r"\bSTANLEY\b|\bBLACK\s*&\s*DECKER\b",
    "Craftsman": r"\bCRAFTSMAN\b",
    "Greenlee": r"\bGREENLEE\b",
    "Channellock": r"\bCHANNELLOCK\b",
    "Irwin": r"\bIRWIN\b",
    "Lenox Tools": r"\bLENOX\b",
}

# Known MPN prefix families -> Brand
MPN_PREFIX_MAP = {
    # Appliances
    "PDSH": "Frigidaire",
    "WDTS": "Whirlpool",
    "KDFM": "Kitchen Aid",
    "KDTS": "Kitchen Aid",
    "KDPS": "Kitchen Aid",
    "KDTM": "Kitchen Aid",
    "KDFS": "Kitchen Aid",
    "PDT": "GE",
    "PDD": "GE",
    "LDPH": "LG",
    "LFXS": "LG",
    "WM40": "LG",
    "DVE": "Samsung",
    "SHE": "Bosch",
    "SHP": "Bosch",
    # Plumbing
    "K-": "Kohler",
    "7594": "Moen",
    "9178": "Delta",
    # Tools
    "2804-": "Milwaukee",
    "DCD": "DeWalt",
    # Electrical
    "HOM": "Square D",
    "QO": "Square D",
    "BR": "Eaton",
}

PLACEHOLDER_BRAND_VALUES = frozenset({
    "-- unbranded --",
    "-- no unilog brand --",
    "-- no dib brand --",
    "n/a",
    "na",
    "null",
    "none",
    "unknown",
    "tbd",
    "?",
    "-",
})


@dataclass
class BrandResolution:
    mpn: str
    brand: str | None
    confidence: str      # "high" | "medium" | "needs_review"
    source: str           # how it was resolved, for audit/debug


def resolve_brand(
    mfg_part_num: str,
    part_desc: str,
    raw_row: dict[str, str] | None = None,
) -> BrandResolution:
    """Resolve the real manufacturer brand for any product row across industrial sectors."""

    # Signal 1: Check explicit brand keywords in Part_Desc (highest confidence)
    for brand, pattern in BRAND_KEYWORDS.items():
        if re.search(pattern, part_desc, re.IGNORECASE):
            return BrandResolution(
                mpn=mfg_part_num,
                brand=brand,
                confidence="high",
                source=f"text_match:'{brand}'",
            )

    # Signal 2: Check raw row brand columns (E1_Brand, Unilog_Brand, DIB_Brand) if available
    if raw_row:
        for brand_col in ["Unilog_Brand", "E1_Brand", "DIB_Brand", "Part_Manuf"]:
            val = (raw_row.get(brand_col) or "").strip()
            val_lower = val.lower()
            if val and val_lower not in PLACEHOLDER_BRAND_VALUES and not val_lower.startswith("--"):
                # Ensure it's not a known distributor cluster (e.g. Appliance Dealers Cooperative)
                if "COOPERATIVE" not in val.upper() and "APPDE" not in val.upper() and "DISTRIBUTOR" not in val.upper():
                    # Check if matches known brand dictionary
                    for brand, pattern in BRAND_KEYWORDS.items():
                        if re.search(pattern, val, re.IGNORECASE):
                            return BrandResolution(
                                mpn=mfg_part_num,
                                brand=brand,
                                confidence="high",
                                source=f"row_col:{brand_col}",
                            )
                    # Use cleaned supplier string as brand candidate
                    return BrandResolution(
                        mpn=mfg_part_num,
                        brand=val,
                        confidence="medium",
                        source=f"row_col:{brand_col}",
                    )

    # Signal 3: MPN prefix patterns
    mpn_clean = mfg_part_num.strip().upper()
    for prefix, brand in MPN_PREFIX_MAP.items():
        if mpn_clean.startswith(prefix.upper()):
            return BrandResolution(
                mpn=mfg_part_num,
                brand=brand,
                confidence="medium",
                source=f"mpn_prefix:'{prefix}'",
            )

    # Signal 4: Leading word in description if it looks like a brand name
    desc_words = part_desc.strip().split()
    if desc_words:
        first_word = desc_words[0].strip("-,:/")
        if len(first_word) >= 3 and first_word.isalpha():
            for brand, pattern in BRAND_KEYWORDS.items():
                if re.search(pattern, first_word, re.IGNORECASE):
                    return BrandResolution(
                        mpn=mfg_part_num,
                        brand=brand,
                        confidence="medium",
                        source=f"first_word:'{first_word}'",
                    )

    # Unresolved - flag for web-grounded research, never guess blindly
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
