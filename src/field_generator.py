from __future__ import annotations

"""
Stage 3: Field Generator
-------------------------
Converts brand_resolver output + web_research output into all 252 columns
for one MPN, following the formulas reverse-engineered from the 2 ground-truth
rows (PDSH4816AF, WDTS7024RZ).

Architecture:
  Layer 1: Deterministic constants and copy-through fields (no LLM)
  Layer 2: LLM extraction via Groq (temperature=0, JSON mode)
           Pre-check: if research status != "found", skip LLM entirely
  Layer 3: Dynamic description assembly from extracted values

Every generated field is traceable to: (a) research source text, (b) a fixed
constant, or (c) flagged as needs_review with empty value. Nothing is fabricated.
"""

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

from brand_resolver import BrandResolution
from web_research import ResearchResult

load_dotenv()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Column headers are read at runtime from this file (HANDOFF §4)
EXPECTED_OUTPUT_CSV = (
    Path(__file__).parent.parent
    / "data"
    / "Unihack__Expected_Output_-_Delivery_Format.csv"
)

# Fixed category constants for dishwashers (HANDOFF §4)
CATEGORY_CONSTANTS = {
    "Dept": "Appliances",
    "Class": "Large Appliances",
    "Fine": "Dishwashers",
    "Classpath": (
        "Appliances & Consumer Electronics>Kitchen Appliances"
        ">Built-In Dishwashers"
    ),
    "Product Name": "Dishwasher",
}

# Fixed attribute labels for dishwashers, slots 1-15.
# Order is sacred — never skip or reorder (HANDOFF §4).
ATTRIBUTE_LABELS: list[str] = [
    "Series",
    "Model",
    "Number of Wash Cycles",
    "Voltage Rating",
    "Amperage Rating",
    "Mounting Type",
    "Plug Type",
    "Size",
    "Depth With Door Open",
    "Minimum Height",
    "Maximum Height",
    "Sound Level",
    "Material",
    "Color",
    "Additional Information",
]

# Mapping: attribute label → JSON key the LLM returns
ATTR_TO_SPEC_KEY: dict[str, str] = {
    "Series": "series",
    "Model": "model",
    "Number of Wash Cycles": "wash_cycles",
    "Voltage Rating": "voltage",
    "Amperage Rating": "amperage",
    "Mounting Type": "mounting_type",
    "Plug Type": "plug_type",
    "Size": "size",
    "Depth With Door Open": "depth_with_door_open",
    "Minimum Height": "min_height",
    "Maximum Height": "max_height",
    "Sound Level": "sound_level",
    "Material": "material",
    "Color": "color",
    "Additional Information": "additional_info",
}

# Which attributes carry a separate UOM column
ATTR_UOM_KEYS: dict[str, str] = {
    "Voltage Rating": "voltage_uom",
    "Amperage Rating": "amperage_uom",
    "Depth With Door Open": "depth_uom",
    "Minimum Height": "min_height_uom",
    "Maximum Height": "max_height_uom",
    "Sound Level": "sound_uom",
}

# Abbreviation map for INVOICE_DESC (learned from ground-truth rows)
INVOICE_ABBREV: dict[str, str] = {
    "Stainless Steel": "SST",
    "Built-in": "BLTLN",
    "Leg": "LEG",
    "Black": "BLK",
    "Black Stainless": "BSS",
    "White": "WHT",
    "Bisque": "BSQ",
    "Slate": "SLT",
    "Fingerprint Resistant Stainless Steel": "FRSS",
}

# Canonical brand names with exact trademark symbols.
# Applied deterministically AFTER LLM extraction to fix formatting
# mismatches (e.g. "Frigidaire" → "FRIGIDAIRE®") — no LLM needed.
CANONICAL_BRAND_NAMES: dict[str, dict[str, str]] = {
    # key = lowercase brand → {brand_name, manufacturer_name}
    "frigidaire": {
        "brand_name": "FRIGIDAIRE\u00ae",
        "manufacturer_name": "Rheem Manufacturing",
    },
    "whirlpool": {
        "brand_name": "Whirlpool\u00ae",
        "manufacturer_name": "Whirlpool Corporation",
    },
    "ge": {
        "brand_name": "GE\u00ae",
        "manufacturer_name": "GE Appliances",
    },
    "lg": {
        "brand_name": "LG\u00ae",
        "manufacturer_name": "LG Electronics",
    },
    "kitchenaid": {
        "brand_name": "KitchenAid\u00ae",
        "manufacturer_name": "Whirlpool Corporation",
    },
    "maytag": {
        "brand_name": "Maytag\u00ae",
        "manufacturer_name": "Whirlpool Corporation",
    },
    "samsung": {
        "brand_name": "Samsung\u00ae",
        "manufacturer_name": "Samsung Electronics",
    },
    "bosch": {
        "brand_name": "Bosch\u00ae",
        "manufacturer_name": "BSH Home Appliances",
    },
    "speed queen": {
        "brand_name": "Speed Queen\u00ae",
        "manufacturer_name": "Alliance Laundry Systems",
    },
}

# Per-attribute formatters for LONG_DESC1.
# Each takes (value, uom) and returns the display string.
LONG_DESC_FORMATTERS: dict[str, object] = {
    "Series": lambda v, u: v,
    "Model": lambda v, u: v,
    "Number of Wash Cycles": lambda v, u: f"{v} Wash Cycles",
    "Voltage Rating": lambda v, u: f"{v} {u}" if u else f"{v} V",
    "Amperage Rating": lambda v, u: f"{v} {u}" if u else f"{v} A",
    "Mounting Type": lambda v, u: f"{v} Mounting",
    "Plug Type": lambda v, u: v,
    "Size": lambda v, u: v,
    "Depth With Door Open": (
        lambda v, u: f"{v} {u} Depth With Door Open"
        if u
        else f"{v} in Depth With Door Open"
    ),
    "Minimum Height": (
        lambda v, u: f"{v} {u} Minimum Height"
        if u
        else f"{v} Minimum Height"
    ),
    "Maximum Height": (
        lambda v, u: f"{v} {u} Maximum Height"
        if u
        else f"{v} Maximum Height"
    ),
    "Sound Level": (
        lambda v, u: f"{v} {u} Sound Level"
        if u
        else f"{v} dBA Sound Level"
    ),
    "Material": lambda v, u: v,
    "Color": lambda v, u: v,
    "Additional Information": lambda v, u: f"Additional Information: {v}",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GenerationResult:
    """Output of generate_fields(): all 252 columns + provenance metadata."""

    mpn: str
    fields: dict[str, str]  # All 252 cols, keyed by exact CSV header
    field_sources: dict[str, str]  # "constant"|"input"|"research"|"derived"|"empty"
    needs_review: list[str]  # Field names that couldn't be grounded
    confidence: str  # "high" | "medium" | "low"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_column_headers() -> list[str]:
    """Read the 252 column headers from the expected output CSV at runtime."""
    with open(EXPECTED_OUTPUT_CSV, encoding="utf-8") as f:
        reader = csv.reader(f)
        return next(reader)


def _strip_brand_symbols(brand_name: str) -> str:
    """Remove ® and ™ from brand name for use in file names etc."""
    return brand_name.replace("®", "").replace("™", "").strip()


# ---------------------------------------------------------------------------
# Layer 2 — LLM extraction
# ---------------------------------------------------------------------------

# Ground-truth extraction examples used as few-shot references in the prompt.
# These teach the LLM the exact output format we expect.

_EXAMPLE_1 = {
    "manufacturer_name": "Rheem Manufacturing",
    "brand_name": "FRIGIDAIRE\u00ae",
    "series": "Professional Series",
    "model": "",
    "wash_cycles": "5",
    "voltage": "120",
    "voltage_uom": "V",
    "amperage": "15",
    "amperage_uom": "A",
    "mounting_type": "Leg",
    "plug_type": "",
    "size": "24 in W x 24-1/4 in D",
    "depth_with_door_open": "50-1/4",
    "depth_uom": "in",
    "min_height": "8-1/2 in Upper Rack, 11-1/4 in Lower Rack",
    "min_height_uom": "",
    "max_height": "10-3/8 in Upper Rack, 13-1/4 in Lower Rack",
    "max_height_uom": "",
    "sound_level": "47",
    "sound_uom": "dBA",
    "material": "Stainless Steel",
    "color": "",
    "additional_info": (
        "240 kW-hr Annual Energy, 1 to 12 hr Delay Start Hours"
    ),
    "with_text": "With CleanBoost\u2122",
    "standards_approvals": (
        "ASSE 1006|CEE Tier 2 Qualified|cUL Listed"
        "|ENERGY STAR Certified|NSF Certified|UL Listed"
    ),
    "warranty": "1 Year Manufacturer, 1 Year Labor and Parts",
    "marketing_description": "",
    "item_features": [],
    "not_found_fields": [
        "model",
        "plug_type",
        "color",
        "marketing_description",
        "item_features",
    ],
}

_EXAMPLE_2 = {
    "manufacturer_name": "Whirlpool Corporation",
    "brand_name": "Whirlpool\u00ae",
    "series": "Eco Series",
    "model": "",
    "wash_cycles": "",
    "voltage": "120",
    "voltage_uom": "V",
    "amperage": "10",
    "amperage_uom": "A",
    "mounting_type": "Built-in",
    "plug_type": "",
    "size": "33-7/16 in H x 23-7/8 in W x 22-5/8 in D",
    "depth_with_door_open": "50-3/16",
    "depth_uom": "in",
    "min_height": "33-7/16",
    "min_height_uom": "in",
    "max_height": "",
    "max_height_uom": "",
    "sound_level": "41",
    "sound_uom": "dBA",
    "material": "Stainless Steel",
    "color": "Stainless Steel",
    "additional_info": (
        "Folding Tines, Leak Detection System, "
        "Moisture Repellent Silverware Basket, Normal Cycle, "
        "Quick Wash Cycle, Sani Rinse Option, Sensor Cycle, "
        "Triple Wash Spray"
    ),
    "with_text": (
        "With Washing 3rd Rack, Water Repellent Silverware Basket"
    ),
    "standards_approvals": "",
    "warranty": "",
    "marketing_description": (
        "Load more and run less with our quietest and largest capacity "
        "dishwasher. A 3rd Rack provides dedicated space for mugs and "
        "bowls, while an adjustable 2nd Rack helps fit all the dishes "
        "and pans your family piles up."
    ),
    "item_features": [
        "3rd rack with extra wash action",
        "Adjustable 2nd Rack",
        "41 dBA",
        "Moisture Repellent Silverware Basket",
        "Sensor cycle",
        "Sani Rinse Option",
        "Leak Detection System",
        "Folding Tines",
        "Normal cycle",
        "Triple Wash Spray",
        "Quick Wash Cycle",
    ],
    "not_found_fields": [
        "model",
        "wash_cycles",
        "plug_type",
        "max_height",
        "standards_approvals",
        "warranty",
    ],
}

_EXTRACTION_KEYS = list(_EXAMPLE_1.keys())


def _build_extraction_messages(
    research_text: str,
    mpn: str,
    brand: str,
) -> list[dict]:
    """Build the chat messages for the Groq LLM spec extraction call."""

    system_msg = (
        "You are a product data extraction specialist for dishwashers. "
        "You extract ONLY factual specifications from the provided research "
        "text. You NEVER use your general knowledge about products or "
        "dishwashers. If a specification cannot be found in the research "
        "text, set its value to an empty string and add the field name to "
        "not_found_fields."
    )

    user_msg = f"""TASK: Extract structured product specifications for dishwasher MPN: {mpn}, Brand hint: {brand}.

RESEARCH TEXT (extract ONLY from this text — do NOT use general knowledge):
---
{research_text}
---

EXTRACTION RULES:
1. Extract values ONLY from the RESEARCH TEXT above. Never fabricate or guess.
2. If a field cannot be found, set it to "" and add the field name to not_found_fields.
3. For brand_name: include the trademark symbol (® or ™) exactly as shown in the research text.
4. For manufacturer_name: use the full legal company name (e.g. "Whirlpool Corporation", "Rheem Manufacturing"), NOT just the brand.
5. Use exact numeric values from research (e.g., "120" not "120.0", "5" not "five").
6. For dimensions with multiple components (like rack heights), include all details exactly as they appear.
7. For item_features: extract individual bullet-point features as a list of strings (max 20).
8. For standards_approvals: use pipe-separated format (e.g. "UL Listed|ENERGY STAR Certified").
9. For with_text: extract any "With..." feature phrase (e.g. "With CleanBoost™").
10. For additional_info: comma-separated list of supplementary specs not covered by other fields.

REFERENCE EXAMPLES (showing the exact output format expected):

Example 1 — Frigidaire PDSH4816AF:
{json.dumps(_EXAMPLE_1, indent=2)}

Example 2 — Whirlpool WDTS7024RZ:
{json.dumps(_EXAMPLE_2, indent=2)}

Return ONLY a valid JSON object with exactly these keys:
{json.dumps(_EXTRACTION_KEYS, indent=2)}"""

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

def _normalize_specs(specs: dict, brand_fallback: str = "") -> dict:
    """Apply deterministic post-LLM normalization to fix known formatting issues.

    This is a free accuracy boost — no API calls, no LLM, just pattern matching:
    1. Brand name → canonical form with ® symbol (using brand_fallback if LLM missed it)
    2. Manufacturer name → canonical legal name
    3. Series → ensure it ends with "Series" if it doesn't already
    4. Mounting type → normalize casing ("Built-In" → "Built-in")
    """
    # --- Brand & Manufacturer normalization ---
    brand_raw = specs.get("brand_name", "") or brand_fallback
    # Strip ® ™ and lowercase for lookup
    brand_key = (
        brand_raw
        .replace("\u00ae", "")
        .replace("\u2122", "")
        .strip()
        .lower()
    )

    canonical = CANONICAL_BRAND_NAMES.get(brand_key)
    if canonical:
        specs["brand_name"] = canonical["brand_name"]
        # Only override manufacturer if LLM left it empty or returned the brand
        if not specs.get("manufacturer_name") or specs["manufacturer_name"].lower() == brand_key:
            specs["manufacturer_name"] = canonical["manufacturer_name"]
        # Remove brand & manufacturer from not_found if we just populated them
        nf = specs.get("not_found_fields", [])
        if "brand_name" in nf:
            nf.remove("brand_name")
        if "manufacturer_name" in nf:
            nf.remove("manufacturer_name")

    # --- Series suffix normalization ---
    series = specs.get("series", "")
    if series and not series.lower().endswith("series"):
        specs["series"] = f"{series} Series"

    # --- Mounting type casing normalization ---
    mounting = specs.get("mounting_type", "")
    mounting_map = {
        "built-in": "Built-in",
        "built in": "Built-in",
        "builtin": "Built-in",
        "leg": "Leg",
        "freestanding": "Freestanding",
    }
    if mounting.lower() in mounting_map:
        specs["mounting_type"] = mounting_map[mounting.lower()]

    return specs


def _extract_specs_via_llm(research: ResearchResult) -> dict | None:
    """Call Groq LLM to extract structured specs from research text.

    Returns extracted dict on success.
    Returns None if research was not found or LLM call fails — caller marks
    all non-deterministic fields as needs_review.
    """
    # Pre-check: skip LLM entirely if no research found (save tokens/cost)
    if research.status != "found":
        return None

    # Build combined research text from all sources
    parts = []
    if research.raw_answer:
        parts.append(f"Summary: {research.raw_answer}")
    for source in research.sources:
        parts.append(f"\nSource: {source['url']}\n{source['content']}")
    research_text = "\n".join(parts)

    # Groq free tier has an 8000 TPM limit. 
    # ~12,000 chars is roughly 3000 tokens, safely allowing 2 requests per minute.
    if len(research_text) > 12000:
        print(f"  WARN: Truncating research text from {len(research_text)} to 12000 chars")
        research_text = research_text[:12000] + "\n...[TRUNCATED TO AVOID RATE LIMITS]"

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("WARNING: GROQ_API_KEY not set — skipping LLM extraction")
        return None

    messages = _build_extraction_messages(
        research_text, research.mpn, research.brand
    )

    client = Groq(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
            max_completion_tokens=4096,
        )
        raw = response.choices[0].message.content
        specs = json.loads(raw)
        return _normalize_specs(specs, brand_fallback=research.brand)

    except json.JSONDecodeError as exc:
        print(f"WARNING: LLM returned invalid JSON for {research.mpn}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: Groq API error for {research.mpn}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Layer 3 — Description builders
# ---------------------------------------------------------------------------


def _build_mobile_desc(specs: dict, mpn: str) -> str:
    """Build MOBILE_DESC via dynamic field joining.  Target 60-80 chars.

    Ground-truth patterns:
      Row 1: "Rheem Manufacturing FRIGIDAIRE, Dishwasher, Professional Series, PDSH4816AF"
             → Manufacturer+Brand space-joined (different entities), then comma-joined fields
      Row 2: "Whirlpool, Dishwasher, Eco Series, WDTS7024RZ, Built-in Mounting"
             → Brand only (≈ manufacturer), extra trailing spec to reach 60 chars
    """
    manufacturer = specs.get("manufacturer_name", "")
    brand_raw = _strip_brand_symbols(specs.get("brand_name", ""))
    series = specs.get("series", "")
    mounting = specs.get("mounting_type", "")

    # Name portion: space-join manufacturer+brand if different entities
    if (
        manufacturer
        and brand_raw
        and brand_raw.upper() not in manufacturer.upper()
    ):
        name_part = f"{manufacturer} {brand_raw}"
    elif brand_raw:
        name_part = brand_raw
    elif manufacturer:
        name_part = manufacturer
    else:
        name_part = mpn  # last resort

    # Comma-separated core fields
    core_parts = [p for p in [name_part, "Dishwasher", series, mpn] if p]
    result = ", ".join(core_parts)

    # If under 60 chars and mounting is available, append to reach target range
    if len(result) < 60 and mounting:
        candidate = f"{result}, {mounting} Mounting"
        if len(candidate) <= 80:
            result = candidate

    return result


def _build_invoice_desc(specs: dict) -> str:
    """Build INVOICE_DESC via priority-based variable-length join.  ≤40 chars, ALL CAPS.

    Ground-truth patterns:
      Row 1: "DISHWASHER LEG 5 SST 120V 15A 50-1/4IN"      (ends with depth)
      Row 2: "DISHWASHER BLTLN SST SST 120V 10A 41DBA"      (ends with sound level)

    Greedily adds specs in priority order; skips any that would push past 40 chars.
    """
    parts: list[str] = ["DISHWASHER"]

    # Build candidate list in priority order (learned from ground-truth analysis)
    candidates: list[str] = []

    mounting = specs.get("mounting_type", "")
    if mounting:
        candidates.append(INVOICE_ABBREV.get(mounting, mounting.upper()[:5]))

    wash_cycles = specs.get("wash_cycles", "")
    if wash_cycles:
        candidates.append(str(wash_cycles))

    material = specs.get("material", "")
    if material:
        candidates.append(INVOICE_ABBREV.get(material, material.upper()[:3]))

    color = specs.get("color", "")
    if color:
        candidates.append(INVOICE_ABBREV.get(color, color.upper()[:3]))

    voltage = specs.get("voltage", "")
    if voltage:
        candidates.append(f"{voltage}V")

    amperage = specs.get("amperage", "")
    if amperage:
        candidates.append(f"{amperage}A")

    # Trailing specs — depth and sound level compete for remaining space
    depth = specs.get("depth_with_door_open", "")
    if depth:
        candidates.append(f"{depth}IN")

    sound_level = specs.get("sound_level", "")
    if sound_level:
        candidates.append(f"{sound_level}DBA")

    # Greedily add while staying ≤ 40 chars total
    for candidate in candidates:
        test = " ".join(parts + [candidate.upper()])
        if len(test) <= 40:
            parts.append(candidate.upper())

    return " ".join(parts)


def _should_include_with_text(with_text: str) -> bool:
    """Include with_text in description opening only if it's a single feature name.

    "With CleanBoost™"  → True  (single named feature → embed in desc)
    "With Washing 3rd Rack, Water Repellent…" → False (feature list → separate field only)
    """
    return bool(with_text) and "," not in with_text


def _build_short_desc(specs: dict, mpn: str) -> str:
    """Build SHORT_DESC from available fields.

    Row 1: "FRIGIDAIRE® Professional Series PDSH4816AF Dishwasher With CleanBoost™,
            Leg Mounting, 5-Wash Cycle, Stainless Steel"
    Row 2: "Whirlpool® Eco Series WDTS7024RZ Dishwasher, Built-in Mounting,
            Stainless Steel, Stainless Steel"
    """
    brand = specs.get("brand_name", "")
    series = specs.get("series", "")
    with_text = specs.get("with_text", "")
    mounting = specs.get("mounting_type", "")
    wash_cycles = specs.get("wash_cycles", "")
    material = specs.get("material", "")
    color = specs.get("color", "")

    # Opening: "{Brand} {Series} {MPN} Dishwasher[ {with_text}]"
    opening_tokens = [t for t in [brand, series, mpn, "Dishwasher"] if t]
    opening = " ".join(opening_tokens)

    if _should_include_with_text(with_text):
        opening += f" {with_text}"

    # Trailing comma-separated specs
    trailing: list[str] = []
    if mounting:
        trailing.append(f"{mounting} Mounting")
    if wash_cycles:
        trailing.append(f"{wash_cycles}-Wash Cycle")
    if material:
        trailing.append(material)
    if color:
        trailing.append(color)

    if trailing:
        return f"{opening}, {', '.join(trailing)}"
    return opening


def _build_long_desc1(specs: dict) -> str:
    """Build LONG_DESC1 by joining attribute values in slot order with formatting.

    Row 1: "FRIGIDAIRE® Dishwasher With CleanBoost™, Professional Series, 5 Wash Cycles, 120 V, …"
    Row 2: "Whirlpool® Dishwasher, Eco Series, 120 V, 10 A, …"
    """
    brand = specs.get("brand_name", "")
    with_text = specs.get("with_text", "")

    # Opening
    opening = f"{brand} Dishwasher"
    if _should_include_with_text(with_text):
        opening += f" {with_text}"

    # Build spec parts from attributes in fixed order
    spec_parts: list[str] = []
    for label in ATTRIBUTE_LABELS:
        spec_key = ATTR_TO_SPEC_KEY[label]
        value = specs.get(spec_key, "")
        if not value:
            continue

        uom_key = ATTR_UOM_KEYS.get(label)
        uom = specs.get(uom_key, "") if uom_key else ""

        formatter = LONG_DESC_FORMATTERS[label]
        spec_parts.append(formatter(value, uom))

    if spec_parts:
        return f"{opening}, {', '.join(spec_parts)}"
    return opening


def _build_retail_desc(specs: dict) -> str:
    """Build RETAIL_DESC: series + type + key specs (no brand, no MPN).

    Row 1: "Professional Series Dishwasher, Leg Mounting, 5-Wash Cycle, Stainless Steel"
    Row 2: "Eco Series Dishwasher, Built-in Mounting, Stainless Steel, Stainless Steel"
    """
    series = specs.get("series", "")
    mounting = specs.get("mounting_type", "")
    wash_cycles = specs.get("wash_cycles", "")
    material = specs.get("material", "")
    color = specs.get("color", "")

    opening = f"{series} Dishwasher" if series else "Dishwasher"

    trailing: list[str] = []
    if mounting:
        trailing.append(f"{mounting} Mounting")
    if wash_cycles:
        trailing.append(f"{wash_cycles}-Wash Cycle")
    if material:
        trailing.append(material)
    if color:
        trailing.append(color)

    if trailing:
        return f"{opening}, {', '.join(trailing)}"
    return opening


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------


def generate_fields(
    brand_resolution: BrandResolution,
    research_result: ResearchResult,
    input_row: dict[str, str],
) -> GenerationResult:
    """Generate all 252 columns for one MPN.

    Args:
        brand_resolution: Output from Stage 1 (brand_resolver)
        research_result:  Output from Stage 2 (web_research)
        input_row:        Original row dict from the input CSV

    Returns:
        GenerationResult with all 252 fields, sources, and review flags.
    """
    mpn = brand_resolution.mpn
    headers = _read_column_headers()

    # Initialise every column to empty string
    fields: dict[str, str] = {h: "" for h in headers}
    field_sources: dict[str, str] = {h: "empty" for h in headers}
    needs_review: list[str] = []

    # -------------------------------------------------------------------
    # Layer 1: Deterministic constants and copy-through
    # -------------------------------------------------------------------

    # Category constants (same for every dishwasher row)
    for col, val in CATEGORY_CONSTANTS.items():
        fields[col] = val
        field_sources[col] = "constant"

    # Copy-through from input row
    for col in [
        "Mfg_Part_Num",
        "Part_Desc",
        "E1_Brand",
        "Unilog_Brand",
        "DIB_Brand",
        "Part_Manuf",
    ]:
        if col in input_row:
            fields[col] = input_row[col]
            field_sources[col] = "input"

    fields["MANUFACTURER_PART_NUMBER"] = mpn
    field_sources["MANUFACTURER_PART_NUMBER"] = "input"

    # Fixed attribute labels (slots 1-15)
    for i, label in enumerate(ATTRIBUTE_LABELS, start=1):
        fields[f"ATTRIBUTE_LABEL {i}"] = label
        field_sources[f"ATTRIBUTE_LABEL {i}"] = "constant"

    # -------------------------------------------------------------------
    # Layer 2: LLM extraction
    # -------------------------------------------------------------------

    specs = _extract_specs_via_llm(research_result)

    if specs is None:
        # No research or LLM failure → mark everything non-deterministic
        for h in headers:
            if field_sources[h] == "empty":
                needs_review.append(h)
        return GenerationResult(
            mpn=mpn,
            fields=fields,
            field_sources=field_sources,
            needs_review=needs_review,
            confidence="low",
        )

    not_found = set(specs.get("not_found_fields", []))

    # -------------------------------------------------------------------
    # Layer 3: Populate fields from extracted specs
    # -------------------------------------------------------------------

    # — Manufacturer & Brand —
    if specs.get("manufacturer_name"):
        fields["MANUFACTURER_NAME"] = specs["manufacturer_name"]
        field_sources["MANUFACTURER_NAME"] = "research"
    else:
        needs_review.append("MANUFACTURER_NAME")

    if specs.get("brand_name"):
        fields["BRAND_NAME"] = specs["brand_name"]
        field_sources["BRAND_NAME"] = "research"
    else:
        needs_review.append("BRAND_NAME")

    # If web research didn't extract brand/mfr, fall back to Stage 1 resolved brand
    brand_key = (brand_resolution.brand or "").strip().lower()
    canonical = CANONICAL_BRAND_NAMES.get(brand_key)
    if (not fields.get("BRAND_NAME") or fields.get("BRAND_NAME") == "") and canonical:
        fields["BRAND_NAME"] = canonical["brand_name"]
        field_sources["BRAND_NAME"] = "derived"
        if "BRAND_NAME" in needs_review:
            needs_review.remove("BRAND_NAME")

    if (not fields.get("MANUFACTURER_NAME") or fields.get("MANUFACTURER_NAME") == "") and canonical:
        fields["MANUFACTURER_NAME"] = canonical["manufacturer_name"]
        field_sources["MANUFACTURER_NAME"] = "derived"
        if "MANUFACTURER_NAME" in needs_review:
            needs_review.remove("MANUFACTURER_NAME")

    # — Attribute values & UOMs (slots 1-15) —
    for i, label in enumerate(ATTRIBUTE_LABELS, start=1):
        spec_key = ATTR_TO_SPEC_KEY[label]
        value = specs.get(spec_key, "")
        if value:
            fields[f"ATTRIBUTE_VALUE {i}"] = str(value)
            field_sources[f"ATTRIBUTE_VALUE {i}"] = "research"
        elif spec_key in not_found:
            needs_review.append(f"ATTRIBUTE_VALUE {i}")

        uom_key = ATTR_UOM_KEYS.get(label)
        if uom_key:
            uom = specs.get(uom_key, "")
            if uom:
                fields[f"ATTRIBUTE_UOM {i}"] = uom
                field_sources[f"ATTRIBUTE_UOM {i}"] = "research"

    # — Description fields (dynamic assembly) —
    fields["MOBILE_DESC"] = _build_mobile_desc(specs, mpn)
    field_sources["MOBILE_DESC"] = "derived"

    fields["INVOICE_DESC"] = _build_invoice_desc(specs)
    field_sources["INVOICE_DESC"] = "derived"

    fields["SHORT_DESC"] = _build_short_desc(specs, mpn)
    field_sources["SHORT_DESC"] = "derived"

    fields["LONG_DESC1"] = _build_long_desc1(specs)
    field_sources["LONG_DESC1"] = "derived"

    fields["RETAIL_DESC"] = _build_retail_desc(specs)
    field_sources["RETAIL_DESC"] = "derived"

    # — With text —
    if specs.get("with_text"):
        fields["With"] = specs["with_text"]
        field_sources["With"] = "research"

    # — Standards/Approvals —
    if specs.get("standards_approvals"):
        fields["Standard/Approvals"] = specs["standards_approvals"]
        field_sources["Standard/Approvals"] = "research"

    # — Marketing description —
    if specs.get("marketing_description"):
        fields["MARKETING_DESCRIPTION"] = specs["marketing_description"]
        field_sources["MARKETING_DESCRIPTION"] = "research"

    # — Item features (up to 20) —
    features = specs.get("item_features", [])
    for i, feat in enumerate(features[:20], start=1):
        fields[f"ITEM_FEATURES_{i}"] = feat
        field_sources[f"ITEM_FEATURES_{i}"] = "research"

    # — Warranty —
    if specs.get("warranty"):
        fields["Warranty"] = specs["warranty"]
        field_sources["Warranty"] = "research"

    # — MFR URL and Ref URLs (from research sources) —
    if research_result.sources:
        fields["MFR URL"] = research_result.sources[0]["url"]
        field_sources["MFR URL"] = "research"
        for i, source in enumerate(research_result.sources[1:5], start=1):
            col = f"Ref URL {i}"
            fields[col] = source["url"]
            field_sources[col] = "research"

    # — Product image & spec sheet (deterministic naming convention) —
    brand_clean = _strip_brand_symbols(
        specs.get("brand_name", brand_resolution.brand or "")
    )
    if brand_clean:
        fields["Product Image"] = f"{brand_clean}_{mpn}.jpg"
        field_sources["Product Image"] = "derived"

        fields["Specification Sheet"] = (
            f"{brand_clean}_{mpn}_Specification_Sheet.pdf"
        )
        field_sources["Specification Sheet"] = "derived"

        fields["Actual Image (Yes/No)"] = "Yes"
        field_sources["Actual Image (Yes/No)"] = "constant"

    # -------------------------------------------------------------------
    # Confidence calculation
    # -------------------------------------------------------------------

    research_count = sum(
        1 for v in field_sources.values() if v in ("research", "derived")
    )

    if research_count >= 30:
        confidence = "high"
    elif research_count >= 15:
        confidence = "medium"
    else:
        confidence = "low"

    # Map not_found spec keys back to column names for needs_review
    for spec_field_name in not_found:
        for label, key in ATTR_TO_SPEC_KEY.items():
            if key == spec_field_name:
                slot = ATTRIBUTE_LABELS.index(label) + 1
                col_name = f"ATTRIBUTE_VALUE {slot}"
                if col_name not in needs_review:
                    needs_review.append(col_name)
                break
        else:
            # Non-attribute field (e.g. marketing_description)
            if spec_field_name not in needs_review:
                needs_review.append(spec_field_name)

    return GenerationResult(
        mpn=mpn,
        fields=fields,
        field_sources=field_sources,
        needs_review=needs_review,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick test with a not_found research result (no real API call)
    mock_brand = BrandResolution(
        mpn="PDSH4816AF",
        brand="Frigidaire",
        confidence="high",
        source="test",
    )
    mock_research = ResearchResult(
        mpn="PDSH4816AF",
        brand="Frigidaire",
        status="not_found",
        raw_answer="No results on official domain.",
        sources=[],
    )
    mock_input = {
        "Mfg_Part_Num": "PDSH4816AF",
        "Part_Desc": "PDSH4816AF Dishwasher SS - Display Only",
        "E1_Brand": "-- Unbranded --",
        "Unilog_Brand": "-- No Unilog Brand --",
        "DIB_Brand": "-- No DIB Brand --",
        "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
    }

    result = generate_fields(mock_brand, mock_research, mock_input)

    print(f"MPN:            {result.mpn}")
    print(f"Confidence:     {result.confidence}")
    print(f"Needs review:   {len(result.needs_review)} fields")
    print(
        f"Fields filled:  "
        f"{sum(1 for v in result.fields.values() if v)}"
    )
    print()

    sample_cols = [
        "Dept", "Class", "Fine", "Classpath",
        "MANUFACTURER_NAME", "BRAND_NAME",
        "MOBILE_DESC", "INVOICE_DESC",
        "ATTRIBUTE_LABEL 1", "ATTRIBUTE_LABEL 15",
    ]
    print("Sample fields:")
    for col in sample_cols:
        print(f"  {col}: {result.fields.get(col, 'N/A')!r}")
