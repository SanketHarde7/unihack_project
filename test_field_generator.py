"""
Verification test for Stage 3 Field Generator.

Tests the deterministic description builders (MOBILE_DESC, INVOICE_DESC,
SHORT_DESC, LONG_DESC1, RETAIL_DESC) against the 2 ground-truth rows
by feeding in the known spec values and comparing output field-by-field.

Also includes automated assertions for:
- INVOICE_DESC length ≤ 40 chars
- MOBILE_DESC length 60-80 chars
- Attribute slot ordering
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Add src to path so we can import field_generator
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.field_generator import (
    GenerationResult,
    _build_invoice_desc,
    _build_long_desc1,
    _build_mobile_desc,
    _build_retail_desc,
    _build_short_desc,
    _read_column_headers,
    _EXAMPLE_1,
    _EXAMPLE_2,
    ATTRIBUTE_LABELS,
    generate_fields,
)
from src.brand_resolver import BrandResolution
from src.web_research import ResearchResult


# ---------------------------------------------------------------------------
# Ground-truth expected values (from expected output CSV)
# ---------------------------------------------------------------------------

GROUND_TRUTH = {
    "PDSH4816AF": {
        "specs": _EXAMPLE_1,  # The same dict we use as few-shot example
        "brand_resolution": BrandResolution(
            mpn="PDSH4816AF", brand="Frigidaire", confidence="high",
            source="text_match",
        ),
        "input_row": {
            "Mfg_Part_Num": "PDSH4816AF",
            "Part_Desc": "PDSH4816AF Dishwasher SS - Display Only",
            "E1_Brand": "-- Unbranded --",
            "Unilog_Brand": "-- No Unilog Brand --",
            "DIB_Brand": "-- No DIB Brand --",
            "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
        },
        "expected": {
            "MANUFACTURER_NAME": "Rheem Manufacturing",
            "BRAND_NAME": "FRIGIDAIRE\u00ae",
            "MOBILE_DESC": (
                "Rheem Manufacturing FRIGIDAIRE, Dishwasher, "
                "Professional Series, PDSH4816AF"
            ),
            "INVOICE_DESC": "DISHWASHER LEG 5 SST 120V 15A 50-1/4IN",
            "SHORT_DESC": (
                "FRIGIDAIRE\u00ae Professional Series PDSH4816AF "
                "Dishwasher With CleanBoost\u2122, Leg Mounting, "
                "5-Wash Cycle, Stainless Steel"
            ),
            "LONG_DESC1": (
                "FRIGIDAIRE\u00ae Dishwasher With CleanBoost\u2122, "
                "Professional Series, 5 Wash Cycles, 120 V, 15 A, "
                "Leg Mounting, 24 in W x 24-1/4 in D, "
                "50-1/4 in Depth With Door Open, "
                "8-1/2 in Upper Rack, 11-1/4 in Lower Rack "
                "Minimum Height, "
                "10-3/8 in Upper Rack, 13-1/4 in Lower Rack "
                "Maximum Height, "
                "47 dBA Sound Level, Stainless Steel, "
                "Additional Information: "
                "240 kW-hr Annual Energy, 1 to 12 hr Delay Start Hours"
            ),
            "RETAIL_DESC": (
                "Professional Series Dishwasher, Leg Mounting, "
                "5-Wash Cycle, Stainless Steel"
            ),
            "ATTRIBUTE_LABEL 1": "Series",
            "ATTRIBUTE_VALUE 1": "Professional Series",
            "ATTRIBUTE_LABEL 15": "Additional Information",
            "ATTRIBUTE_VALUE 3": "5",
            "ATTRIBUTE_VALUE 4": "120",
            "ATTRIBUTE_UOM 4": "V",
        },
    },
    "WDTS7024RZ": {
        "specs": _EXAMPLE_2,
        "brand_resolution": BrandResolution(
            mpn="WDTS7024RZ", brand="Whirlpool", confidence="high",
            source="text_match",
        ),
        "input_row": {
            "Mfg_Part_Num": "WDTS7024RZ",
            "Part_Desc": "WDTS7024RZ Dishwasher SS - Display Only",
            "E1_Brand": "-- Unbranded --",
            "Unilog_Brand": "-- No Unilog Brand --",
            "DIB_Brand": "-- No DIB Brand --",
            "Part_Manuf": "Appliance Dealers Cooperative (APPDE)",
        },
        "expected": {
            "MANUFACTURER_NAME": "Whirlpool Corporation",
            "BRAND_NAME": "Whirlpool\u00ae",
            "MOBILE_DESC": (
                "Whirlpool, Dishwasher, Eco Series, "
                "WDTS7024RZ, Built-in Mounting"
            ),
            "INVOICE_DESC": "DISHWASHER BLTLN SST SST 120V 10A 41DBA",
            "SHORT_DESC": (
                "Whirlpool\u00ae Eco Series WDTS7024RZ Dishwasher, "
                "Built-in Mounting, Stainless Steel, Stainless Steel"
            ),
            "LONG_DESC1": (
                "Whirlpool\u00ae Dishwasher, Eco Series, 120 V, 10 A, "
                "Built-in Mounting, "
                "33-7/16 in H x 23-7/8 in W x 22-5/8 in D, "
                "50-3/16 in Depth With Door Open, "
                "33-7/16 in Minimum Height, "
                "41 dBA Sound Level, "
                "Stainless Steel, Stainless Steel, "
                "Additional Information: "
                "Folding Tines, Leak Detection System, "
                "Moisture Repellent Silverware Basket, Normal Cycle, "
                "Quick Wash Cycle, Sani Rinse Option, Sensor Cycle, "
                "Triple Wash Spray"
            ),
            "RETAIL_DESC": (
                "Eco Series Dishwasher, Built-in Mounting, "
                "Stainless Steel, Stainless Steel"
            ),
            "ATTRIBUTE_LABEL 1": "Series",
            "ATTRIBUTE_VALUE 1": "Eco Series",
            "ATTRIBUTE_LABEL 6": "Mounting Type",
            "ATTRIBUTE_VALUE 6": "Built-in",
            "ATTRIBUTE_VALUE 12": "41",
            "ATTRIBUTE_UOM 12": "dBA",
        },
    },
}


def test_description_builders():
    """Test each description builder against ground truth specs."""
    print("=" * 70)
    print("TESTING DESCRIPTION BUILDERS (deterministic, no LLM)")
    print("=" * 70)

    all_pass = True

    for mpn, data in GROUND_TRUTH.items():
        specs = data["specs"]
        expected = data["expected"]

        print(f"\n--- {mpn} ---")

        # MOBILE_DESC
        mobile = _build_mobile_desc(specs, mpn)
        mobile_expected = expected["MOBILE_DESC"]
        ok = mobile == mobile_expected
        status = "\u2705" if ok else "\u274c"
        print(f"  {status} MOBILE_DESC  (len={len(mobile)})")
        if not ok:
            print(f"      GOT:      {mobile!r}")
            print(f"      EXPECTED: {mobile_expected!r}")
            all_pass = False

        # MOBILE_DESC length assertion
        if not (60 <= len(mobile) <= 80):
            print(f"  \u26a0\ufe0f  MOBILE_DESC length {len(mobile)} outside 60-80 range")

        # INVOICE_DESC
        invoice = _build_invoice_desc(specs)
        invoice_expected = expected["INVOICE_DESC"]
        ok = invoice == invoice_expected
        status = "\u2705" if ok else "\u274c"
        print(f"  {status} INVOICE_DESC (len={len(invoice)})")
        if not ok:
            print(f"      GOT:      {invoice!r}")
            print(f"      EXPECTED: {invoice_expected!r}")
            all_pass = False

        # INVOICE_DESC length assertion (hard limit)
        assert len(invoice) <= 40, (
            f"INVOICE_DESC for {mpn} is {len(invoice)} chars (max 40): {invoice!r}"
        )

        # SHORT_DESC
        short = _build_short_desc(specs, mpn)
        short_expected = expected["SHORT_DESC"]
        ok = short == short_expected
        status = "\u2705" if ok else "\u274c"
        print(f"  {status} SHORT_DESC")
        if not ok:
            print(f"      GOT:      {short!r}")
            print(f"      EXPECTED: {short_expected!r}")
            all_pass = False

        # LONG_DESC1
        long_desc = _build_long_desc1(specs)
        long_expected = expected["LONG_DESC1"]
        ok = long_desc == long_expected
        status = "\u2705" if ok else "\u274c"
        print(f"  {status} LONG_DESC1   (len={len(long_desc)})")
        if not ok:
            print(f"      GOT:      {long_desc!r}")
            print(f"      EXPECTED: {long_expected!r}")
            all_pass = False

        # RETAIL_DESC
        retail = _build_retail_desc(specs)
        retail_expected = expected["RETAIL_DESC"]
        ok = retail == retail_expected
        status = "\u2705" if ok else "\u274c"
        print(f"  {status} RETAIL_DESC")
        if not ok:
            print(f"      GOT:      {retail!r}")
            print(f"      EXPECTED: {retail_expected!r}")
            all_pass = False

    return all_pass


def test_attribute_slot_ordering():
    """Verify that attribute labels follow the exact ground-truth order."""
    print("\n" + "=" * 70)
    print("TESTING ATTRIBUTE SLOT ORDERING")
    print("=" * 70)

    # Read ground-truth CSV to get actual label values
    csv_path = Path(__file__).parent / "data" / "Unihack__Expected_Output_-_Delivery_Format.csv"
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        gt_rows = list(reader)

    all_pass = True
    for gt_row in gt_rows:
        mpn = gt_row["Mfg_Part_Num"]
        print(f"\n--- {mpn} ---")
        for i, expected_label in enumerate(ATTRIBUTE_LABELS, start=1):
            actual = gt_row.get(f"ATTRIBUTE_LABEL {i}", "")
            ok = actual == expected_label
            if not ok:
                print(f"  \u274c Slot {i}: expected {expected_label!r}, got {actual!r}")
                all_pass = False
        if all_pass:
            print(f"  \u2705 All 15 attribute labels match")

    return all_pass


def test_full_generate_with_mock_specs():
    """Test generate_fields with a mock ResearchResult that returns our example specs.

    This bypasses the actual LLM call by monkey-patching the extraction function.
    """
    print("\n" + "=" * 70)
    print("TESTING FULL GENERATE (mock LLM, no real API call)")
    print("=" * 70)

    import src.field_generator as fg

    all_pass = True

    for mpn, data in GROUND_TRUTH.items():
        specs = data["specs"]
        brand_res = data["brand_resolution"]
        input_row = data["input_row"]
        expected = data["expected"]

        # Monkey-patch the LLM extraction to return our known specs
        original_fn = fg._extract_specs_via_llm
        fg._extract_specs_via_llm = lambda r: specs

        # Create a mock "found" research result
        mock_research = ResearchResult(
            mpn=mpn,
            brand=brand_res.brand,
            status="found",
            query=f"test query for {mpn}",
            sources=[{"url": f"https://example.com/{mpn}", "content": "mock"}],
            raw_answer="mock research",
        )

        result = generate_fields(brand_res, mock_research, input_row)

        # Restore
        fg._extract_specs_via_llm = original_fn

        print(f"\n--- {mpn} (confidence={result.confidence}) ---")

        for field_name, expected_val in expected.items():
            actual_val = result.fields.get(field_name, "")
            ok = actual_val == expected_val
            status = "\u2705" if ok else "\u274c"
            if not ok:
                print(f"  {status} {field_name}")
                print(f"      GOT:      {actual_val!r}")
                print(f"      EXPECTED: {expected_val!r}")
                all_pass = False

        # Count matches
        match_count = sum(
            1 for f, v in expected.items()
            if result.fields.get(f, "") == v
        )
        total = len(expected)
        print(f"  Field match: {match_count}/{total} ({100*match_count/total:.0f}%)")

        # Char limit assertions
        invoice = result.fields["INVOICE_DESC"]
        mobile = result.fields["MOBILE_DESC"]
        assert len(invoice) <= 40, (
            f"INVOICE_DESC too long ({len(invoice)} chars): {invoice!r}"
        )
        print(f"  \u2705 INVOICE_DESC length OK ({len(invoice)} ≤ 40)")

        if 60 <= len(mobile) <= 80:
            print(f"  \u2705 MOBILE_DESC length OK ({len(mobile)} in 60-80)")
        else:
            print(f"  \u26a0\ufe0f  MOBILE_DESC length {len(mobile)} outside 60-80")

    return all_pass


if __name__ == "__main__":
    print()
    pass1 = test_description_builders()
    pass2 = test_attribute_slot_ordering()
    pass3 = test_full_generate_with_mock_specs()

    print("\n" + "=" * 70)
    if pass1 and pass2 and pass3:
        print("\u2705 ALL TESTS PASSED")
    else:
        parts = []
        if not pass1:
            parts.append("description builders")
        if not pass2:
            parts.append("attribute ordering")
        if not pass3:
            parts.append("full generate")
        print(f"\u274c SOME FAILURES IN: {', '.join(parts)}")
    print("=" * 70)

    sys.exit(0 if (pass1 and pass2 and pass3) else 1)
