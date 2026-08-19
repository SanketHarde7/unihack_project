"""
Enterprise Multi-Channel Syndication Exporter
--------------------------------------------
Transforms master enriched catalog records into major downstream enterprise formats:
  1. Unilog Master 252-Column Standard CSV
  2. Grainger Industrial B2B Standard CSV
  3. Shopify E-Commerce Catalog CSV
  4. Universal JSON PIM Schema
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any


def export_to_unilog(records: list[dict[str, Any]], headers: list[str]) -> str:
    """Generate Unilog Ground-Truth 252-Column Master CSV."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for rec in records:
        fields = rec.get("fields", rec)
        row = {h: fields.get(h, "") for h in headers}
        writer.writerow(row)
    return output.getvalue()


def export_to_grainger(records: list[dict[str, Any]]) -> str:
    """Generate Grainger Industrial B2B Standard Catalog CSV."""
    grainger_headers = [
        "Item",
        "Manufacturer Name",
        "Brand",
        "Mfr. Model #",
        "UNSPSC",
        "Item Type",
        "Key Specifications",
        "Voltage",
        "Amperage",
        "Dimensions",
        "Standards / Approvals",
        "Warranty Description",
        "Primary Image URL",
        "Spec Sheet URL",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=grainger_headers)
    writer.writeheader()

    for rec in records:
        fields = rec.get("fields", rec)
        mpn = fields.get("Mfg_Part_Num", fields.get("MANUFACTURER_PART_NUMBER", "UNKNOWN"))
        brand = fields.get("BRAND_NAME", fields.get("Part_Manuf", ""))
        mfr = fields.get("MANUFACTURER_NAME", brand)
        
        # Build spec string from first 5 slots
        spec_parts = []
        for i in range(1, 6):
            lbl = fields.get(f"ATTRIBUTE_LABEL {i}", "")
            val = fields.get(f"ATTRIBUTE_VALUE {i}", "")
            uom = fields.get(f"ATTRIBUTE_UOM {i}", "")
            if lbl and val:
                spec_parts.append(f"{lbl}: {val} {uom}".strip())

        row = {
            "Item": mpn,
            "Manufacturer Name": mfr,
            "Brand": brand,
            "Mfr. Model #": mpn,
            "UNSPSC": fields.get("UNSPSC", "47121804"),
            "Item Type": fields.get("Product Name", "Industrial Product"),
            "Key Specifications": "; ".join(spec_parts),
            "Voltage": fields.get("ATTRIBUTE_VALUE 4", ""),
            "Amperage": fields.get("ATTRIBUTE_VALUE 5", ""),
            "Dimensions": f"{fields.get('WIDTH', '')} x {fields.get('HEIGHT', '')} x {fields.get('LENGTH', '')}".strip(" x"),
            "Standards / Approvals": fields.get("Standard/Approvals", ""),
            "Warranty Description": fields.get("Warranty", "1 Year Limited Warranty"),
            "Primary Image URL": fields.get("Product Image", ""),
            "Spec Sheet URL": fields.get("Specification Sheet", fields.get("MFR URL", "")),
        }
        writer.writerow(row)

    return output.getvalue()


def export_to_shopify(records: list[dict[str, Any]]) -> str:
    """Generate Shopify Modern E-Commerce Standard Catalog CSV."""
    shopify_headers = [
        "Handle",
        "Title",
        "Body (HTML)",
        "Vendor",
        "Product Category",
        "Type",
        "Tags",
        "Published",
        "Option1 Name",
        "Option1 Value",
        "Variant SKU",
        "Variant Grams",
        "Variant Inventory Tracker",
        "Variant Price",
        "Variant Requires Shipping",
        "Variant Taxable",
        "Image Src",
        "Status",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=shopify_headers)
    writer.writeheader()

    for rec in records:
        fields = rec.get("fields", rec)
        mpn = fields.get("Mfg_Part_Num", fields.get("MANUFACTURER_PART_NUMBER", "UNKNOWN"))
        brand = fields.get("BRAND_NAME", "Brand")
        title = fields.get("SHORT_DESC", f"{brand} {mpn}")
        handle = re.sub(r"[^a-z0-9]+", "-", f"{brand}-{mpn}".lower()).strip("-")
        
        # Build HTML description
        mktg = fields.get("MARKETING_DESCRIPTION", "")
        features = [fields.get(f"ITEM_FEATURES_{i}", "") for i in range(1, 10) if fields.get(f"ITEM_FEATURES_{i}")]
        feat_html = "".join([f"<li>{f}</li>" for f in features])
        body_html = f"<p>{mktg}</p><ul>{feat_html}</ul>" if feat_html else f"<p>{mktg}</p>"

        tags = f"{brand}, {fields.get('Product Name', '')}, {fields.get('Class', '')}".strip(", ")

        row = {
            "Handle": handle,
            "Title": title,
            "Body (HTML)": body_html,
            "Vendor": brand,
            "Product Category": fields.get("Classpath", "Appliances"),
            "Type": fields.get("Product Name", "General"),
            "Tags": tags,
            "Published": "TRUE",
            "Option1 Name": "Title",
            "Option1 Value": "Default Title",
            "Variant SKU": mpn,
            "Variant Grams": "5000",
            "Variant Inventory Tracker": "shopify",
            "Variant Price": fields.get("List Price", "0.00") or "0.00",
            "Variant Requires Shipping": "TRUE",
            "Variant Taxable": "TRUE",
            "Image Src": fields.get("Product Image", ""),
            "Status": "active",
        }
        writer.writerow(row)

    return output.getvalue()


def export_to_json_pim(records: list[dict[str, Any]]) -> str:
    """Generate Universal REST-Ready JSON PIM Payload."""
    payload = []
    for rec in records:
        fields = rec.get("fields", rec)
        mpn = fields.get("Mfg_Part_Num", fields.get("MANUFACTURER_PART_NUMBER", "UNKNOWN"))
        
        attributes = {}
        for i in range(1, 16):
            lbl = fields.get(f"ATTRIBUTE_LABEL {i}", "")
            val = fields.get(f"ATTRIBUTE_VALUE {i}", "")
            uom = fields.get(f"ATTRIBUTE_UOM {i}", "")
            if lbl and val:
                attributes[lbl] = {"value": val, "uom": uom}

        item_obj = {
            "mfg_part_number": mpn,
            "brand": fields.get("BRAND_NAME", ""),
            "manufacturer": fields.get("MANUFACTURER_NAME", ""),
            "taxonomy": {
                "department": fields.get("Dept", ""),
                "class": fields.get("Class", ""),
                "fine": fields.get("Fine", ""),
                "classpath": fields.get("Classpath", ""),
                "product_name": fields.get("Product Name", ""),
            },
            "descriptions": {
                "invoice": fields.get("INVOICE_DESC", ""),
                "mobile": fields.get("MOBILE_DESC", ""),
                "short": fields.get("SHORT_DESC", ""),
                "long": fields.get("LONG_DESC1", ""),
                "retail": fields.get("RETAIL_DESC", ""),
            },
            "attributes": attributes,
            "compliance": {
                "standards": fields.get("Standard/Approvals", ""),
                "warranty": fields.get("Warranty", ""),
                "prop65": fields.get("Prop 65", ""),
            },
            "digital_assets": {
                "mfr_url": fields.get("MFR URL", ""),
                "primary_image": fields.get("Product Image", ""),
                "spec_sheet": fields.get("Specification Sheet", ""),
            },
        }
        payload.append(item_obj)

    return json.dumps(payload, indent=2)
