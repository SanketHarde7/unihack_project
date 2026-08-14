"""
Stage 5: End-to-End Batch Pipeline Runner & Exporter
----------------------------------------------------
Orchestrates the entire catalog enrichment pipeline across raw input datasets:
  Stage 1: Brand Resolution (brand_resolver.py)
  Stage 2: Web Research & Spec Fetching (web_research.py)
  Stage 3: 252-Column Field Generation (field_generator.py)
  Stage 4: Enterprise Quality Validation & Scoring (validator.py)

Generates:
  1. output/Unihack_Enriched_Catalog_Delivery.csv (252 static columns, exact delivery format)
  2. output/enrichment_audit_report.json (provenance map, fill rates, confidence metrics)

Includes built-in rate-limit auto-pacer to respect Groq and Tavily free-tier limits.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from brand_resolver import resolve_brand
from field_generator import _read_column_headers, generate_fields
from validator import validate_enriched_record
from web_research import research_product


def load_input_rows(input_path: Path, category_filter: str = "dishwasher") -> list[dict[str, str]]:
    """Load and filter candidate rows from the input CSV."""
    with open(input_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    if not category_filter:
        return all_rows

    filtered = [
        row for row in all_rows
        if category_filter.lower() in row.get("Part_Desc", "").lower()
    ]
    return filtered


def run_pipeline(
    input_csv: Path,
    output_csv: Path,
    output_audit: Path,
    limit: int = 10,
    category: str = "dishwasher",
    single_mpn: str | None = None,
    pace_seconds: int = 25,
) -> dict:
    """Run the complete enrichment pipeline on the dataset."""

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_audit.parent.mkdir(parents=True, exist_ok=True)

    headers = _read_column_headers()
    rows = load_input_rows(input_csv, category_filter=category)

    if single_mpn:
        rows = [r for r in rows if r.get("Mfg_Part_Num", "").strip().upper() == single_mpn.strip().upper()]

    if limit and limit > 0:
        rows = rows[:limit]

    print("=" * 80)
    print(f"🚀 STARTING UNIHACK CATALOG ENRICHMENT PIPELINE")
    print(f"   Input items to process: {len(rows)}")
    print(f"   Target category:        {category or 'All'}")
    print(f"   Delivery format schema: 252 static columns")
    print("=" * 80 + "\n")

    enriched_records: list[dict[str, str]] = []
    audit_reports: list[dict] = []
    start_time = time.time()

    for idx, row in enumerate(rows, start=1):
        mpn = row.get("Mfg_Part_Num", "UNKNOWN")
        desc = row.get("Part_Desc", "")

        print(f"[{idx}/{len(rows)}] Processing MPN: {mpn} ('{desc}')")

        # --- Stage 1: Brand Resolution ---
        brand_res = resolve_brand(mpn, desc)
        print(f"  Stage 1 (Brand):     {brand_res.brand} (conf={brand_res.confidence}, src={brand_res.source})")

        # --- Stage 2: Web Research ---
        if brand_res.brand:
            print(f"  Stage 2 (Research):  Searching official domain for '{brand_res.brand}'...")
            research_res = research_product(mpn, brand_res.brand, product_type=category)
            print(f"                       Status: {research_res.status}, Sources: {len(research_res.sources)}")
        else:
            from web_research import ResearchResult
            research_res = ResearchResult(mpn=mpn, brand="", status="not_found", raw_answer="No brand resolved")
            print(f"  Stage 2 (Research):  Skipped (Brand unresolved)")

        # --- Stage 3: Field Generation ---
        print(f"  Stage 3 (Generator): Generating 252 columns...")
        gen_res = generate_fields(brand_res, research_res, row)

        # --- Stage 4: Validation & Quality Assurance ---
        print(f"  Stage 4 (Validator): Running enterprise PIM validation...")
        val_summary = validate_enriched_record(gen_res, brand_res, research_res)

        status_emoji = "✅" if val_summary.is_valid else "⚠️"
        print(f"                       {status_emoji} Valid: {val_summary.is_valid}, Confidence: {val_summary.confidence} ({val_summary.confidence_score*100:.1f}%)")
        print(f"                       Needs Review: {len(val_summary.needs_review)} fields")

        # Guarantee exact 252 header key ordering
        ordered_row = {h: gen_res.fields.get(h, "") for h in headers}
        enriched_records.append(ordered_row)

        audit_reports.append({
            "mpn": mpn,
            "description": desc,
            "resolved_brand": brand_res.brand,
            "brand_confidence": brand_res.confidence,
            "research_status": research_res.status,
            "sources_count": len(research_res.sources),
            "sources": [s["url"] for s in research_res.sources],
            "validation": {
                "is_valid": val_summary.is_valid,
                "confidence": val_summary.confidence,
                "confidence_score": val_summary.confidence_score,
                "score_breakdown": val_summary.score_breakdown,
                "issues": [asdict(i) for i in val_summary.issues],
                "needs_review_fields": val_summary.needs_review,
            },
            "field_provenance_summary": {
                source_type: sum(1 for v in gen_res.field_sources.values() if v == source_type)
                for source_type in ("constant", "input", "research", "derived", "empty")
            },
        })

        print("-" * 80)

        # Pacing to respect Groq 8,000 TPM limit
        if idx < len(rows) and pace_seconds > 0 and research_res.status == "found":
            print(f"⏳ Pacing {pace_seconds}s for API rate limits...\n")
            time.sleep(pace_seconds)

    # --- Write Delivery CSV ---
    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(enriched_records)

    # --- Write Audit Report JSON ---
    elapsed_time = round(time.time() - start_time, 2)
    overall_metrics = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_items_processed": len(enriched_records),
        "execution_time_seconds": elapsed_time,
        "high_confidence_count": sum(1 for a in audit_reports if a["validation"]["confidence"] == "HIGH"),
        "medium_confidence_count": sum(1 for a in audit_reports if a["validation"]["confidence"] == "MEDIUM"),
        "low_confidence_count": sum(1 for a in audit_reports if a["validation"]["confidence"] == "LOW"),
        "valid_records_count": sum(1 for a in audit_reports if a["validation"]["is_valid"]),
        "delivery_csv_path": str(output_csv),
        "total_columns": len(headers),
        "items": audit_reports,
    }

    with open(output_audit, "w", encoding="utf-8") as f:
        json.dump(overall_metrics, f, indent=2)

    print("\n" + "=" * 80)
    print("🎉 PIPELINE RUN COMPLETE")
    print(f"   Enriched CSV saved:  {output_csv}")
    print(f"   Audit JSON saved:     {output_audit}")
    print(f"   High Confidence:      {overall_metrics['high_confidence_count']}/{len(enriched_records)}")
    print(f"   Medium Confidence:    {overall_metrics['medium_confidence_count']}/{len(enriched_records)}")
    print(f"   Low Confidence:       {overall_metrics['low_confidence_count']}/{len(enriched_records)}")
    print(f"   Execution time:       {elapsed_time}s")
    print("=" * 80)

    return overall_metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UniHack Catalog Enrichment Pipeline Runner")
    parser.add_argument("--limit", type=int, default=10, help="Number of rows to process")
    parser.add_argument("--category", type=str, default="dishwasher", help="Filter by product category")
    parser.add_argument("--mpn", type=str, default=None, help="Process single MPN")
    parser.add_argument("--pace", type=int, default=15, help="Pacing sleep in seconds between live calls")
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path(__file__).parent.parent / "data" / "Unihack__Sample_Dataset_-_Input.csv",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path(__file__).parent.parent / "output" / "Unihack_Enriched_Catalog_Delivery.csv",
    )
    parser.add_argument(
        "--output-audit",
        type=Path,
        default=Path(__file__).parent.parent / "output" / "enrichment_audit_report.json",
    )

    args = parser.parse_args()
    run_pipeline(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        output_audit=args.output_audit,
        limit=args.limit,
        category=args.category,
        single_mpn=args.mpn,
        pace_seconds=args.pace,
    )
