import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brand_resolver import resolve_brand
from web_research import research_product, ResearchResult
from field_generator import generate_fields, detect_product_category, TAXONOMY_CATALOG, _read_column_headers
from validator import validate_enriched_record
from database import save_record
import csv

input_csv = Path(__file__).parent / "data" / "supervisor_challenge_dataset.csv"
output_csv = Path(__file__).parent / "output" / "supervisor_enriched_catalog.csv"
output_audit = Path(__file__).parent / "output" / "supervisor_audit_report.json"

output_csv.parent.mkdir(parents=True, exist_ok=True)
headers = _read_column_headers()

with open(input_csv, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

enriched_rows = []
audit_records = []

print(f"Running Supervisor Challenge Batch on {len(rows)} items...\n")

for idx, row in enumerate(rows, 1):
    mpn = row["Mfg_Part_Num"].strip()
    desc = row.get("Part_Desc", "").strip()
    print(f"[{idx}/{len(rows)}] Processing {mpn} ({desc[:40]}...)...")

    # Stage 1: Brand & Category
    brand_res = resolve_brand(mpn, desc, raw_row=row)
    category_key = detect_product_category(mpn, desc)
    print(f"   Brand: {brand_res.brand} | Category: {category_key}")

    # Stage 2: Web Research
    if brand_res.brand:
        research_res = research_product(mpn, brand_res.brand, product_type=category_key)
        print(f"   Research Status: {research_res.status} | Sources: {len(research_res.sources)}")
    else:
        research_res = ResearchResult(mpn=mpn, brand="", status="not_found", raw_answer="No brand")

    # Stage 3: Field Generation
    gen_res = generate_fields(brand_res, research_res, row)
    
    # Stage 4: Validator
    val_summary = validate_enriched_record(gen_res, brand_res, research_res)

    enriched_rows.append(gen_res.fields)
    
    audit_item = {
        "mpn": mpn,
        "description": desc,
        "resolved_brand": brand_res.brand,
        "category": category_key,
        "is_valid": val_summary.is_valid,
        "confidence": val_summary.confidence,
        "confidence_score": val_summary.confidence_score,
        "fields": gen_res.fields,
        "sources": [s["url"] for s in research_res.sources],
        "validation": {
            "is_valid": val_summary.is_valid,
            "confidence": val_summary.confidence,
            "confidence_score": val_summary.confidence_score,
            "issues": [{"field": i.field, "type": i.issue_type, "message": i.message, "rule": i.rule_code} for i in val_summary.issues],
            "needs_review_fields": val_summary.needs_review,
        }
    }
    audit_records.append(audit_item)
    save_record(
        mpn,
        brand_res.brand,
        gen_res.fields,
        [s["url"] for s in research_res.sources],
        asdict(val_summary),
    )

    # Short pause to prevent API rate limiting
    time.sleep(3)

# Write output CSV
with open(output_csv, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    for er in enriched_rows:
        writer.writerow({h: er.get(h, "") for h in headers})

# Write audit JSON
with open(output_audit, "w", encoding="utf-8") as f:
    json.dump(audit_records, f, indent=2)

print("\nSupervisor Challenge Batch complete!")
