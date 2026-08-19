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
import re
import time
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

# ---------------------------------------------------------------------------
# Unilog Decimal to Fraction Lookup Table (Decimal_Fraction.xlsx)
# ---------------------------------------------------------------------------

DECIMAL_FRACTION_MAP: dict[float, str] = {
    0.015625: "1/64", 0.03125: "1/32", 0.046875: "3/64", 0.0625: "1/16",
    0.078125: "5/64", 0.09375: "3/32", 0.109375: "7/64", 0.125: "1/8",
    0.140625: "9/64", 0.15625: "5/32", 0.171875: "11/64", 0.1875: "3/16",
    0.203125: "13/64", 0.21875: "7/32", 0.234375: "15/64", 0.25: "1/4",
    0.265625: "17/64", 0.28125: "9/32", 0.296875: "19/64", 0.3125: "5/16",
    0.328125: "21/64", 0.34375: "11/32", 0.359375: "23/64", 0.375: "3/8",
    0.390625: "25/64", 0.40625: "13/32", 0.421875: "27/64", 0.4375: "7/16",
    0.453125: "29/64", 0.46875: "15/32", 0.484375: "31/64", 0.5: "1/2",
    0.515625: "33/64", 0.53125: "17/32", 0.546875: "35/64", 0.5625: "9/16",
    0.578125: "37/64", 0.59375: "19/32", 0.609375: "39/64", 0.625: "5/8",
    0.640625: "41/64", 0.65625: "21/32", 0.671875: "43/64", 0.6875: "11/16",
    0.703125: "45/64", 0.71875: "23/32", 0.734375: "47/64", 0.75: "3/4",
    0.765625: "49/64", 0.78125: "25/32", 0.796875: "51/64", 0.8125: "13/16",
    0.828125: "53/64", 0.84375: "27/32", 0.859375: "55/64", 0.875: "7/8",
    0.890625: "57/64", 0.90625: "29/32", 0.921875: "59/64", 0.9375: "15/16",
    0.953125: "61/64", 0.96875: "31/32", 0.984375: "63/64",
}


def format_decimal_to_fraction(val_str: str) -> str:
    """Convert decimal inches to standard trade fractions (e.g. 50.25 -> 50-1/4, 24.125 -> 24-1/8)."""
    if not val_str:
        return ""
    val_clean = str(val_str).strip()
    match = re.match(r"^(\d+)?(?:\s*-\s*|\s+)?\.(\d+)$|^(\d+)\.(\d+)$", val_clean)
    if not match:
        return val_clean
    try:
        whole = int(match.group(1) or match.group(3) or 0)
        dec_digits = match.group(2) or match.group(4) or "0"
        dec_float = float(f"0.{dec_digits}")

        closest_frac = None
        min_diff = 0.015
        for dec_val, frac_str in DECIMAL_FRACTION_MAP.items():
            diff = abs(dec_float - dec_val)
            if diff < min_diff:
                min_diff = diff
                closest_frac = frac_str

        if closest_frac:
            return f"{whole}-{closest_frac}" if whole > 0 else closest_frac
        return val_clean
    except Exception:
        return val_clean


# ---------------------------------------------------------------------------
# Multi-Category Taxonomy Catalog & Classifier
# ---------------------------------------------------------------------------

TAXONOMY_CATALOG: dict[str, dict[str, Any]] = {
    "dishwasher": {
        "Dept": "Appliances",
        "Class": "Large Appliances",
        "Fine": "Dishwashers",
        "Classpath": "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers",
        "Product Name": "Dishwasher",
        "labels": ATTRIBUTE_LABELS,
    },
    "faucet": {
        "Dept": "Plumbing",
        "Class": "Plumbing Fixtures",
        "Fine": "Faucets",
        "Classpath": "Plumbing Fixtures>Faucets>Kitchen Faucets",
        "Product Name": "Kitchen Faucet",
        "labels": [
            "Series", "Model", "Flow Rate", "Number of Handles", "Finish",
            "Valve Type", "Spout Type", "Spout Reach", "Spout Height", "Mounting Type",
            "Number of Faucet Holes", "Material", "Color", "Drain Included", "Additional Information",
        ],
    },
    "fitting": {
        "Dept": "Pipes, Valves & Fittings",
        "Class": "Pipe Fittings",
        "Fine": "Couplings & Adapters",
        "Classpath": "Pipes, Valves & Fittings>Pipe Fittings>Couplings",
        "Product Name": "Pipe Fitting",
        "labels": [
            "Fitting Type", "Connection Type 1", "Connection Type 2", "Nominal Size 1", "Nominal Size 2",
            "Material Construction", "Pressure Rating", "Schedule", "Finish", "Standards & Approvals",
            "Length", "Outside Diameter", "Thread Type", "Color", "Additional Information",
        ],
    },
    "refrigerator": {
        "Dept": "Appliances",
        "Class": "Large Appliances",
        "Fine": "Refrigerators",
        "Classpath": "Appliances & Consumer Electronics>Kitchen Appliances>Refrigerators",
        "Product Name": "Refrigerator",
        "labels": [
            "Series", "Model", "Total Capacity", "Refrigerator Capacity", "Freezer Capacity",
            "Defrost Type", "Number of Doors", "Ice Maker", "Voltage Rating", "Width",
            "Depth", "Height", "Energy Star", "Color", "Additional Information",
        ],
    },
    "washer": {
        "Dept": "Appliances",
        "Class": "Laundry Appliances",
        "Fine": "Washing Machines",
        "Classpath": "Appliances & Consumer Electronics>Laundry Appliances>Washing Machines",
        "Product Name": "Washing Machine",
        "labels": [
            "Series", "Model", "Capacity", "Load Type", "Number of Wash Cycles",
            "Maximum Spin Speed", "Voltage Rating", "Amperage Rating", "Width", "Depth",
            "Height", "Steam Function", "Energy Star", "Color", "Additional Information",
        ],
    },
    "range": {
        "Dept": "Appliances",
        "Class": "Cooking Appliances",
        "Fine": "Ranges & Ovens",
        "Classpath": "Appliances & Consumer Electronics>Cooking Appliances>Ranges",
        "Product Name": "Range",
        "labels": [
            "Series", "Model", "Fuel Type", "Number of Burners", "Oven Capacity",
            "Cleaning Type", "Cooktop Surface", "Voltage Rating", "Amperage Rating", "Width",
            "Depth", "Height", "Convection", "Color", "Additional Information",
        ],
    },
    "tool": {
        "Dept": "Tools & Hardware",
        "Class": "Power Tools",
        "Fine": "Drills & Drivers",
        "Classpath": "Tools & Hardware>Power Tools>Drills & Drivers",
        "Product Name": "Power Tool",
        "labels": [
            "Series", "Model", "Voltage Rating", "Chuck Size", "Maximum Speed",
            "Torque", "Battery Type", "Motor Type", "Length", "Weight",
            "Tool Power Output", "Chuck Type", "Housing Material", "Color", "Additional Information",
        ],
    },
    "electrical": {
        "Dept": "Electrical Distribution",
        "Class": "Circuit Breakers",
        "Fine": "Molded Case Circuit Breakers",
        "Classpath": "Electrical Distribution>Circuit Breakers>Molded Case Circuit Breakers",
        "Product Name": "Circuit Breaker",
        "labels": [
            "Series", "Model", "Current Rating", "Voltage Rating", "Number of Poles",
            "Interrupt Rating", "Mounting Type", "Trip Type", "Wire Size", "Frame Size",
            "Frequency Rating", "Operating Temperature", "Standards", "Phase", "Additional Information",
        ],
    },
    "hvac": {
        "Dept": "Heating, Ventilation & Air Conditioning",
        "Class": "Air Conditioning Equipment",
        "Fine": "Split Systems",
        "Classpath": "Heating, Ventilation & Air Conditioning>Air Conditioning Equipment>Split Systems",
        "Product Name": "Air Conditioner",
        "labels": [
            "Series", "Model", "Cooling Capacity", "SEER Rating", "Voltage Rating",
            "Refrigerant Type", "Sound Level", "Compressor Type", "Width", "Depth",
            "Height", "Phase", "Energy Star", "Color", "Additional Information",
        ],
    },
    "water_heater": {
        "Dept": "Plumbing",
        "Class": "Water Heaters",
        "Fine": "Residential Water Heaters",
        "Classpath": "Plumbing Fixtures>Water Heaters>Residential Water Heaters",
        "Product Name": "Water Heater",
        "labels": [
            "Series", "Model", "Tank Capacity", "Fuel Type", "Energy Factor",
            "First Hour Rating", "Voltage Rating", "Vent Type", "Height", "Diameter",
            "Recovery Rate", "Warranty", "Standards", "Color", "Additional Information",
        ],
    },
}


def detect_product_category(mpn: str, desc: str) -> str:
    """Detect category classification from MPN and description text."""
    combined = f"{mpn} {desc}".lower()
    mpn_up = mpn.strip().upper()

    if re.search(r"\b(dishwasher|dish\s*washer|dw)\b", combined) or any(mpn_up.startswith(p) for p in ["PDSH", "WDTS", "KDFM", "KDTS", "KDPS", "KDTM", "KDFS", "PDT", "PDD", "LDPH", "SHE", "SHP"]):
        return "dishwasher"
    if re.search(r"\b(refrigerator|fridge|freezer|french\s*door|side-by-side)\b", combined) or mpn_up.startswith("LFXS"):
        return "refrigerator"
    if re.search(r"\b(faucet|lavatory|spout|sink\s*trim)\b", combined) or mpn_up.startswith("K-") or "7594" in mpn_up:
        return "faucet"
    if re.search(r"\b(fitting|coupling|elbow|cplg|nipple|adapter|valve)\b", combined) or re.search(r"\btee\b", combined):
        return "fitting"
    if re.search(r"\b(washer|dryer|laundry|front\s*load|top\s*load)\b", combined) or mpn_up.startswith("WM40") or mpn_up.startswith("DVE"):
        return "washer"
    if re.search(r"\b(range|oven|cooktop|stove|microwave)\b", combined):
        return "range"
    if re.search(r"\b(drill|saw|driver|wrench|multimeter|hammer\s*drill|tool)\b", combined) or mpn_up.startswith("2804-") or mpn_up.startswith("DCD"):
        return "tool"
    if re.search(r"\b(breaker|circuit|switch|receptacle|panelboard|fuse)\b", combined) or any(mpn_up.startswith(p) for p in ["HOM", "QO", "BR"]):
        return "electrical"
    if re.search(r"\b(ac|air\s*conditioner|heat\s*pump|furnace|thermostat)\b", combined):
        return "hvac"
    if re.search(r"\b(water\s*heater|tankless|geyser)\b", combined):
        return "water_heater"
    return "dishwasher"


# Canonical brand names with exact trademark symbols and parent manufacturer names.
CANONICAL_BRAND_NAMES: dict[str, dict[str, str]] = {
    # Major Appliances
    "frigidaire": {"brand_name": "FRIGIDAIRE\u00ae", "manufacturer_name": "Rheem Manufacturing"},
    "whirlpool": {"brand_name": "Whirlpool\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "ge": {"brand_name": "GE\u00ae", "manufacturer_name": "GE Appliances"},
    "lg": {"brand_name": "LG\u00ae", "manufacturer_name": "LG Electronics"},
    "kitchenaid": {"brand_name": "KitchenAid\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "kitchen aid": {"brand_name": "KitchenAid\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "maytag": {"brand_name": "Maytag\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "samsung": {"brand_name": "Samsung\u00ae", "manufacturer_name": "Samsung Electronics"},
    "bosch": {"brand_name": "Bosch\u00ae", "manufacturer_name": "BSH Home Appliances"},
    "speed queen": {"brand_name": "Speed Queen\u00ae", "manufacturer_name": "Alliance Laundry Systems"},
    "electrolux": {"brand_name": "Electrolux\u00ae", "manufacturer_name": "Electrolux Home Products"},
    "haier": {"brand_name": "Haier\u00ae", "manufacturer_name": "GE Appliances"},
    "miele": {"brand_name": "Miele\u00ae", "manufacturer_name": "Miele, Inc."},
    "jennair": {"brand_name": "JennAir\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "amana": {"brand_name": "Amana\u00ae", "manufacturer_name": "Whirlpool Corporation"},
    "viking": {"brand_name": "Viking\u00ae", "manufacturer_name": "Viking Range, LLC"},
    "sub-zero": {"brand_name": "Sub-Zero\u00ae", "manufacturer_name": "Sub-Zero Group, Inc."},
    "wolf": {"brand_name": "Wolf\u00ae", "manufacturer_name": "Sub-Zero Group, Inc."},
    "dacor": {"brand_name": "Dacor\u00ae", "manufacturer_name": "Samsung Electronics"},
    "sharp": {"brand_name": "Sharp\u00ae", "manufacturer_name": "Sharp Electronics Corporation"},

    # Plumbing & Fixtures (FAUCETS_LOV)
    "kohler": {"brand_name": "KOHLER\u00ae", "manufacturer_name": "Kohler Co."},
    "moen": {"brand_name": "Moen\u00ae", "manufacturer_name": "Fortune Brands Innovations"},
    "delta": {"brand_name": "Delta\u00ae", "manufacturer_name": "Masco Corporation"},
    "american standard": {"brand_name": "American Standard\u00ae", "manufacturer_name": "LIXIL Corporation"},
    "pfister": {"brand_name": "Pfister\u00ae", "manufacturer_name": "Spectrum Brands, Inc."},
    "grohe": {"brand_name": "GROHE\u00ae", "manufacturer_name": "LIXIL Corporation"},
    "hansgrohe": {"brand_name": "hansgrohe\u00ae", "manufacturer_name": "Hansgrohe SE"},
    "toto": {"brand_name": "TOTO\u00ae", "manufacturer_name": "TOTO USA, Inc."},
    "sloan": {"brand_name": "Sloan\u00ae", "manufacturer_name": "Sloan Valve Company"},
    "zurn": {"brand_name": "Zurn\u00ae", "manufacturer_name": "Zurn Elkay Water Solutions"},
    "watts": {"brand_name": "Watts\u00ae", "manufacturer_name": "Watts Water Technologies"},
    "elkay": {"brand_name": "Elkay\u00ae", "manufacturer_name": "Zurn Elkay Water Solutions"},

    # Pipes & Fittings (Fittings_LOV)
    "apollo valves": {"brand_name": "Apollo\u00ae Valves", "manufacturer_name": "Aalberts Piping Systems"},
    "nibco": {"brand_name": "NIBCO\u00ae", "manufacturer_name": "NIBCO INC."},
    "charlotte pipe": {"brand_name": "Charlotte Pipe\u00ae", "manufacturer_name": "Charlotte Pipe and Foundry Company"},
    "sharkbite": {"brand_name": "SharkBite\u00ae", "manufacturer_name": "Reliance Worldwide Corporation"},
    "viega": {"brand_name": "Viega\u00ae", "manufacturer_name": "Viega LLC"},
    "victaulic": {"brand_name": "Victaulic\u00ae", "manufacturer_name": "Victaulic Company"},

    # HVAC & Water Heating
    "carrier": {"brand_name": "Carrier\u00ae", "manufacturer_name": "Carrier Global Corporation"},
    "trane": {"brand_name": "Trane\u00ae", "manufacturer_name": "Trane Technologies"},
    "rheem": {"brand_name": "Rheem\u00ae", "manufacturer_name": "Rheem Manufacturing Company"},
    "lennox": {"brand_name": "Lennox\u00ae", "manufacturer_name": "Lennox International Inc."},
    "goodman": {"brand_name": "Goodman\u00ae", "manufacturer_name": "Daikin Comfort Technologies"},
    "a.o. smith": {"brand_name": "A. O. Smith\u00ae", "manufacturer_name": "A. O. Smith Corporation"},
    "bradford white": {"brand_name": "Bradford White\u00ae", "manufacturer_name": "Bradford White Corporation"},
    "honeywell": {"brand_name": "Honeywell\u00ae", "manufacturer_name": "Resideo Technologies, Inc."},

    # Electrical & Distribution
    "square d": {"brand_name": "Square D\u2122", "manufacturer_name": "Schneider Electric"},
    "schneider electric": {"brand_name": "Schneider Electric\u2122", "manufacturer_name": "Schneider Electric"},
    "eaton": {"brand_name": "Eaton\u00ae", "manufacturer_name": "Eaton Corporation"},
    "siemens": {"brand_name": "Siemens\u00ae", "manufacturer_name": "Siemens Industry, Inc."},
    "leviton": {"brand_name": "Leviton\u00ae", "manufacturer_name": "Leviton Manufacturing Co., Inc."},
    "hubbell": {"brand_name": "Hubbell\u00ae", "manufacturer_name": "Hubbell Incorporated"},
    "lutron": {"brand_name": "Lutron\u00ae", "manufacturer_name": "Lutron Electronics Co., Inc."},

    # Tools & Industrial
    "milwaukee": {"brand_name": "Milwaukee\u00ae", "manufacturer_name": "Techtronic Industries Co. Ltd."},
    "dewalt": {"brand_name": "DEWALT\u00ae", "manufacturer_name": "Stanley Black & Decker, Inc."},
    "makita": {"brand_name": "Makita\u00ae", "manufacturer_name": "Makita Corporation"},
    "klein tools": {"brand_name": "Klein Tools\u00ae", "manufacturer_name": "Klein Tools, Inc."},
    "fluke": {"brand_name": "Fluke\u00ae", "manufacturer_name": "Fortive Corporation"},
    "ridgid": {"brand_name": "RIDGID\u00ae", "manufacturer_name": "Emerson Electric Co."},
    "3m": {"brand_name": "3M\u2122", "manufacturer_name": "3M Company"},
    "stanley": {"brand_name": "STANLEY\u00ae", "manufacturer_name": "Stanley Black & Decker, Inc."},
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


def _clean_source_content(raw_content: str, mpn: str, brand: str) -> str:
    """Clean nav boilerplate from page content, then extract a 1500-char window
    centered around the MPN or brand keyword instead of naive first-1500-chars.
    """
    if not raw_content:
        return ""

    # Strip common boilerplate lines (nav, cookie banners, header cruft)
    boilerplate_patterns = [
        r"^\s*skip to.*$",
        r"^\s*cookie.*$",
        r"^\s*sign in.*$",
        r"^\s*sign up.*$",
        r"^\s*log in.*$",
        r"^\s*my account.*$",
        r"^\s*cart.*$",
        r"^\s*shopping cart.*$",
        r"^\s*menu.*$",
        r"^\s*search.*$",
        r"^\s*home\s*$",
        r"^\s*\|\s*$",
        r"^\s*>\s*$",
        r"^\s*close.*$",
        r"^\s*accept.*cookies.*$",
        r"^\s*we use cookies.*$",
        r"^\s*privacy.*$",
        r"^\s*find a store.*$",
        r"^\s*customer service.*$",
        r"^\s*help\s*$",
        r"^\s*contact us.*$",
    ]
    lines = raw_content.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip very short nav-style lines (likely menu items)
        if len(stripped) < 4:
            continue
        # Skip lines matching boilerplate patterns
        if any(re.match(pat, stripped, re.IGNORECASE) for pat in boilerplate_patterns):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)

    # If content is already short enough, return as-is
    if len(cleaned) <= 1500:
        return cleaned

    # Find the best anchor point: MPN first, then brand name
    anchor_pos = -1
    mpn_upper = mpn.upper()
    cleaned_upper = cleaned.upper()

    anchor_pos = cleaned_upper.find(mpn_upper)
    if anchor_pos == -1 and brand:
        # Try brand name (handle "Kitchen Aid" / "KitchenAid" variants)
        brand_variants = [brand, brand.replace(" ", "")]
        for variant in brand_variants:
            anchor_pos = cleaned_upper.find(variant.upper())
            if anchor_pos != -1:
                break

    if anchor_pos == -1:
        # No keyword found — take from start (fallback)
        return cleaned[:1500]

    # Center a 1500-char window around the anchor
    half_window = 750
    start = max(0, anchor_pos - half_window)
    end = start + 1500
    if end > len(cleaned):
        end = len(cleaned)
        start = max(0, end - 1500)

    return cleaned[start:end]


def _extract_specs_via_llm(research: ResearchResult) -> dict | None:
    """Call Groq LLM to extract structured specs from research text with retry on rate limit.

    Returns extracted dict on success.
    Returns None if research was not found or all retries fail — caller marks
    all non-deterministic fields as needs_review.
    """
    # Pre-check: skip LLM entirely if no research found (save tokens/cost)
    if research.status != "found":
        return None

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("WARNING: GROQ_API_KEY not set — skipping LLM extraction")
        return None

    # Sort sources by relevance score if available, pick top 3
    sorted_sources = sorted(
        research.sources,
        key=lambda s: float(s.get("score", 0.0) or 0.0),
        reverse=True,
    )[:3]

    # Build combined research text from top 3 sources with smart content windowing
    parts = []
    if research.raw_answer:
        parts.append(f"Summary: {research.raw_answer[:1500]}")
    for source in sorted_sources:
        raw_content = source.get("content", "")
        content_snippet = _clean_source_content(raw_content, research.mpn, research.brand)
        url = source.get('url', '')
        parts.append(f"\nSource: {url}\n{content_snippet}")

        # Debug: show first 200 chars of what we're sending to LLM
        print(f"    [DEBUG LLM input] {research.mpn} | {url[:60]}... | first 200 chars: {content_snippet[:200]!r}")
    research_text = "\n".join(parts)

    messages = _build_extraction_messages(
        research_text, research.mpn, research.brand
    )

    client = Groq(api_key=api_key)
    max_retries = 2

    for attempt in range(max_retries + 1):
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
        except Exception as exc:  # Catch RateLimitError, 429, 413, etc.
            exc_str = str(exc).lower()
            is_rate_limit = (
                "rate_limit" in exc_str
                or "tokens per minute" in exc_str
                or "429" in exc_str
                or "413" in exc_str
                or "tpm" in exc_str
                or "too large" in exc_str
            )
            if is_rate_limit and attempt < max_retries:
                # Try to extract retry-after from error string if present
                wait_match = re.search(r"try again in (\d+(\.\d+)?)s", str(exc), re.IGNORECASE)
                if wait_match:
                    wait_secs = int(float(wait_match.group(1))) + 5
                else:
                    wait_secs = 65
                print(
                    f"  ⏳ Rate limit hit for {research.mpn} "
                    f"(attempt {attempt + 1}/{max_retries + 1}). "
                    f"Waiting {wait_secs}s before retry..."
                )
                time.sleep(wait_secs)
                continue

            print(f"WARNING: Groq API error for {research.mpn} (attempt {attempt + 1}): {exc}")
            return None

    return None


# ---------------------------------------------------------------------------
# Layer 3 — Description builders
# ---------------------------------------------------------------------------


def _build_mobile_desc(specs: dict, mpn: str, product_name: str = "Dishwasher") -> str:
    """Build MOBILE_DESC via dynamic field joining. Target 60-80 chars.

    HARD RULE: Never return a string over 80 chars. If over, progressively
    drop mounting suffix -> series -> shorten name_part until it fits.
    Final safety net: hard truncate to 77 + "...".
    """
    manufacturer = specs.get("manufacturer_name", "")
    brand_raw = _strip_brand_symbols(specs.get("brand_name", ""))
    series = specs.get("series", "")
    mounting = specs.get("mounting_type", "")
    prod_type = product_name or "Product"

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

    # --- Attempt 1: full build with mounting suffix ---
    core_parts = [p for p in [name_part, prod_type, series, mpn] if p]
    result = ", ".join(core_parts)

    # If under 60 chars and mounting is available, try appending
    if len(result) < 60 and mounting:
        candidate = f"{result}, {mounting} Mounting"
        if len(candidate) <= 80:
            result = candidate

    # --- Attempt 2: if over 80, drop mounting suffix ---
    if len(result) > 80:
        core_parts = [p for p in [name_part, prod_type, series, mpn] if p]
        result = ", ".join(core_parts)

    # --- Attempt 3: if still over 80, drop series ---
    if len(result) > 80:
        core_parts = [p for p in [name_part, prod_type, mpn] if p]
        result = ", ".join(core_parts)

    # --- Attempt 4: if still over 80, use brand only (drop manufacturer) ---
    if len(result) > 80 and brand_raw:
        core_parts = [p for p in [brand_raw, prod_type, mpn] if p]
        result = ", ".join(core_parts)

    # --- Safety net: hard truncate ---
    if len(result) > 80:
        result = result[:77] + "..."

    return result


def _build_invoice_desc(specs: dict, product_name: str = "DISHWASHER") -> str:
    """Build INVOICE_DESC via priority-based variable-length join. <=40 chars, ALL CAPS.

    Greedily adds specs in priority order; skips any that would push past 40 chars.
    """
    lead_word = (product_name or "ITEM").upper().split()[0]
    parts: list[str] = [lead_word]

    # Build candidate list in priority order
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

    # Trailing specs - depth and sound level compete for remaining space
    depth = format_decimal_to_fraction(specs.get("depth_with_door_open", ""))
    if depth:
        candidates.append(f"{depth}IN".replace(" ", ""))

    sound_level = specs.get("sound_level", "")
    if sound_level:
        candidates.append(f"{sound_level}DBA")

    # Greedily add while staying <= 40 chars total
    for candidate in candidates:
        test = " ".join(parts + [candidate.upper()])
        if len(test) <= 40:
            parts.append(candidate.upper())

    return " ".join(parts)


def _should_include_with_text(with_text: str) -> bool:
    """Include with_text in description opening only if it's a single feature name."""
    return bool(with_text) and "," not in with_text


def _build_short_desc(specs: dict, mpn: str, product_name: str = "Dishwasher") -> str:
    """Build SHORT_DESC from available fields."""
    brand = specs.get("brand_name", "")
    series = specs.get("series", "")
    with_text = specs.get("with_text", "")
    mounting = specs.get("mounting_type", "")
    wash_cycles = specs.get("wash_cycles", "")
    material = specs.get("material", "")
    color = specs.get("color", "")
    prod_type = product_name or "Dishwasher"

    # Opening: "{Brand} {Series} {MPN} {Product}[ {with_text}]"
    opening_tokens = [t for t in [brand, series, mpn, prod_type] if t]
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


def _build_long_desc1(specs: dict, product_name: str = "Dishwasher") -> str:
    """Build LONG_DESC1 by joining attribute values in slot order with formatting."""
    brand = specs.get("brand_name", "")
    with_text = specs.get("with_text", "")
    prod_type = product_name or "Dishwasher"

    # Opening
    opening = f"{brand} {prod_type}" if brand else prod_type
    if _should_include_with_text(with_text):
        opening += f" {with_text}"

    # Build spec parts from attributes in fixed order
    spec_parts: list[str] = []
    for label in ATTRIBUTE_LABELS:
        spec_key = ATTR_TO_SPEC_KEY.get(label, "")
        if not spec_key:
            continue
        value = specs.get(spec_key, "")
        if not value:
            continue

        uom_key = ATTR_UOM_KEYS.get(label)
        uom = specs.get(uom_key, "") if uom_key else ""

        formatter = LONG_DESC_FORMATTERS.get(label)
        if formatter and callable(formatter):
            spec_parts.append(formatter(value, uom))
        else:
            spec_parts.append(f"{value} {uom}".strip())

    if spec_parts:
        return f"{opening}, {', '.join(spec_parts)}"
    return opening


def _build_retail_desc(specs: dict, product_name: str = "Dishwasher") -> str:
    """Build RETAIL_DESC: series + type + key specs (no brand, no MPN)."""
    series = specs.get("series", "")
    mounting = specs.get("mounting_type", "")
    wash_cycles = specs.get("wash_cycles", "")
    material = specs.get("material", "")
    color = specs.get("color", "")
    prod_type = product_name or "Dishwasher"

    opening = f"{series} {prod_type}" if series else prod_type

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

    # Detect product category and taxonomy classification
    desc_text = input_row.get("Part_Desc", "")
    category_key = detect_product_category(mpn, desc_text)
    tax_info = TAXONOMY_CATALOG.get(category_key, TAXONOMY_CATALOG["dishwasher"])
    labels = tax_info.get("labels", ATTRIBUTE_LABELS)
    product_name = tax_info.get("Product Name", "Dishwasher")

    # Category constants (taxonomy mapping per Unilog guidelines)
    for col in ["Dept", "Class", "Fine", "Classpath", "Product Name"]:
        if col in tax_info:
            fields[col] = tax_info[col]
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

    # Attribute labels (slots 1-15)
    for i, label in enumerate(labels[:15], start=1):
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

    # Collect source texts by source type for provenance attribution
    pdf_texts = [
        s.get("content", "").lower()
        for s in research_result.sources
        if s.get("source_type") == "pdf" or s.get("url", "").lower().split("?")[0].endswith(".pdf")
    ]
    html_texts = [
        s.get("content", "").lower()
        for s in research_result.sources
        if s.get("source_type") != "pdf" and not s.get("url", "").lower().split("?")[0].endswith(".pdf")
    ]

    def _determine_research_provenance(value_str: str) -> str:
        """Assign 'research_pdf' if value originates from a PDF technical document, else 'research'."""
        if not value_str or not pdf_texts:
            return "research"
        val_clean = str(value_str).strip().lower()
        # Check if extracted value is found in any PDF source text
        if any(val_clean in pt for pt in pdf_texts):
            if not any(val_clean in ht for ht in html_texts):
                return "research_pdf"
            top_src = research_result.sources[0] if research_result.sources else {}
            if top_src.get("source_type") == "pdf" or top_src.get("url", "").lower().split("?")[0].endswith(".pdf"):
                return "research_pdf"
        return "research"

    # -------------------------------------------------------------------
    # Layer 3: Populate fields from extracted specs
    # -------------------------------------------------------------------

    # — Manufacturer & Brand —
    if specs.get("manufacturer_name"):
        fields["MANUFACTURER_NAME"] = specs["manufacturer_name"]
        field_sources["MANUFACTURER_NAME"] = _determine_research_provenance(specs["manufacturer_name"])
    else:
        needs_review.append("MANUFACTURER_NAME")

    if specs.get("brand_name"):
        fields["BRAND_NAME"] = specs["brand_name"]
        field_sources["BRAND_NAME"] = _determine_research_provenance(specs["brand_name"])
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
            field_sources[f"ATTRIBUTE_VALUE {i}"] = _determine_research_provenance(value)
        elif spec_key in not_found:
            needs_review.append(f"ATTRIBUTE_VALUE {i}")

        uom_key = ATTR_UOM_KEYS.get(label)
        if uom_key:
            uom = specs.get(uom_key, "")
            if uom:
                fields[f"ATTRIBUTE_UOM {i}"] = uom
                field_sources[f"ATTRIBUTE_UOM {i}"] = _determine_research_provenance(uom)

    # — Description fields (dynamic assembly) —
    fields["MOBILE_DESC"] = _build_mobile_desc(specs, mpn, product_name=product_name)
    field_sources["MOBILE_DESC"] = "derived"

    fields["INVOICE_DESC"] = _build_invoice_desc(specs, product_name=product_name)
    field_sources["INVOICE_DESC"] = "derived"

    fields["SHORT_DESC"] = _build_short_desc(specs, mpn, product_name=product_name)
    field_sources["SHORT_DESC"] = "derived"

    fields["LONG_DESC1"] = _build_long_desc1(specs, product_name=product_name)
    field_sources["LONG_DESC1"] = "derived"

    fields["RETAIL_DESC"] = _build_retail_desc(specs, product_name=product_name)
    field_sources["RETAIL_DESC"] = "derived"

    # — With text —
    if specs.get("with_text"):
        fields["With"] = specs["with_text"]
        field_sources["With"] = _determine_research_provenance(specs["with_text"])

    # — Standards/Approvals —
    if specs.get("standards_approvals"):
        fields["Standard/Approvals"] = specs["standards_approvals"]
        field_sources["Standard/Approvals"] = _determine_research_provenance(specs["standards_approvals"])

    # — Marketing description —
    if specs.get("marketing_description"):
        fields["MARKETING_DESCRIPTION"] = specs["marketing_description"]
        field_sources["MARKETING_DESCRIPTION"] = _determine_research_provenance(specs["marketing_description"])

    # — Item features (up to 20) —
    features = specs.get("item_features", [])
    for i, feat in enumerate(features[:20], start=1):
        fields[f"ITEM_FEATURES_{i}"] = feat
        field_sources[f"ITEM_FEATURES_{i}"] = _determine_research_provenance(feat)

    # — Warranty —
    if specs.get("warranty"):
        fields["Warranty"] = specs["warranty"]
        field_sources["Warranty"] = _determine_research_provenance(specs["warranty"])

    # — MFR URL and Ref URLs (from research sources) —
    if research_result.sources:
        top_url = research_result.sources[0]["url"]
        is_pdf_top = top_url.lower().split("?")[0].endswith(".pdf") or research_result.sources[0].get("source_type") == "pdf"
        fields["MFR URL"] = top_url
        field_sources["MFR URL"] = "research_pdf" if is_pdf_top else "research"
        for i, source in enumerate(research_result.sources[1:5], start=1):
            col = f"Ref URL {i}"
            u = source["url"]
            is_pdf_ref = u.lower().split("?")[0].endswith(".pdf") or source.get("source_type") == "pdf"
            fields[col] = u
            field_sources[col] = "research_pdf" if is_pdf_ref else "research"

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
        1 for v in field_sources.values() if v in ("research", "research_pdf", "derived")
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
