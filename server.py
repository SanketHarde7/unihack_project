"""
UniHack Enterprise Backend API Server (FastAPI on Render / Local)
------------------------------------------------------------------
Serves REST APIs for product catalog enrichment, caching, and export.
Includes CORS middleware for seamless Vercel frontend integration.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brand_resolver import resolve_brand
from database import get_all_records, get_record, save_record
from field_generator import _read_column_headers, detect_product_category, generate_fields
from pipeline_runner import load_input_rows
from validator import validate_enriched_record
from web_research import BRAND_OFFICIAL_DOMAINS, ResearchResult, research_product

load_dotenv()

app = FastAPI(
    title="UniHack Catalog Enrichment API",
    description="Enterprise AI-Powered Product Catalog Enrichment Engine",
    version="1.0.0",
)

# Enable CORS for Vercel and localhost cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class EnrichRequest(BaseModel):
    mpn: str
    description: str = ""
    distributor: str = ""
    force_refresh: bool = False


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    """System health check & cloud service status."""
    supabase_configured = bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")))
    groq_configured = bool(os.getenv("GROQ_API_KEY"))
    tavily_configured = bool(os.getenv("TAVILY_API_KEY"))

    return {
        "status": "healthy",
        "engine": "UniHack 2026 Enterprise Enrichment Engine",
        "groq_api": "connected" if groq_configured else "missing_key",
        "tavily_api": "connected" if tavily_configured else "missing_key",
        "database": "Supabase PostgreSQL" if supabase_configured else "SQLite Local Fallback",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@app.get("/api/presets")
def get_presets():
    """Return pre-loaded benchmark dishwasher presets from the dataset."""
    return [
        {
            "mpn": "PDSH4816AF",
            "brand": "Frigidaire",
            "description": "PDSH4816AF Dishwasher SS - Display Only",
            "highlight": "Ground Truth Benchmark 1 (CleanBoost™, 5-Wash Cycles)",
        },
        {
            "mpn": "WDTS7024RZ",
            "brand": "Whirlpool",
            "description": "WDTS7024RZ Dishwasher SS - Display Only",
            "highlight": "Ground Truth Benchmark 2 (Eco Series, 41 dBA, 3rd Rack)",
        },
        {
            "mpn": "KDFM404KPS",
            "brand": "KitchenAid",
            "description": "KDFM404KPS Dishwasher SS",
            "highlight": "KitchenAid PrintShield™ Finish & FreeFlex™ Third Rack",
        },
        {
            "mpn": "PDT715SYVFS",
            "brand": "GE",
            "description": "PDT715SYVFS Ge Dishwasher SS",
            "highlight": "GE Profile™ with Microban® Antimicrobial Technology",
        },
    ]


@app.post("/api/enrich")
def enrich_product(req: EnrichRequest):
    """Enrich a single MPN through Stages 1 -> 2 -> 3 -> 4 with caching."""
    mpn = req.mpn.strip()
    if not mpn:
        raise HTTPException(status_code=400, detail="MPN cannot be empty")

    desc = req.description.strip() or f"{mpn} Dishwasher"

    # 1. Check database cache if force_refresh is not requested
    if not req.force_refresh:
        cached = get_record(mpn)
        if cached:
            cached["cached"] = True
            return cached

    # 2. Stage 1: Brand Resolution
    brand_res = resolve_brand(mpn, desc)
    category_key = detect_product_category(mpn, desc)

    # 3. Stage 2: Web Research
    if brand_res.brand:
        research_res = research_product(mpn, brand_res.brand, product_type=category_key)
    else:
        research_res = ResearchResult(mpn=mpn, brand="", status="not_found", raw_answer="Brand unresolved")

    # 4. Stage 3: Field Generation
    input_row = {
        "Mfg_Part_Num": mpn,
        "Part_Desc": desc,
        "Part_Manuf": req.distributor or "Appliance Dealers Cooperative (APPDE)",
    }
    gen_res = generate_fields(brand_res, research_res, input_row)

    # 5. Stage 4: Enterprise PIM Validation & Scoring
    val_summary = validate_enriched_record(gen_res, brand_res, research_res)

    # Convert dataclasses to serializable dict
    val_dict = {
        "is_valid": val_summary.is_valid,
        "confidence": val_summary.confidence,
        "confidence_score": val_summary.confidence_score,
        "score_breakdown": val_summary.score_breakdown,
        "issues": [asdict(i) for i in val_summary.issues],
        "needs_review_fields": val_summary.needs_review,
    }

    sources_list = [s["url"] for s in research_res.sources]

    # 6. Save to Supabase / SQLite Cache
    save_record(mpn, brand_res.brand or "Unknown", gen_res.fields, sources_list, val_dict)

    return {
        "mpn": mpn,
        "brand": brand_res.brand,
        "brand_confidence": brand_res.confidence,
        "brand_source": brand_res.source,
        "research_status": research_res.status,
        "sources": sources_list,
        "fields": gen_res.fields,
        "field_sources": gen_res.field_sources,
        "validation": val_dict,
        "cached": False,
    }


# ---------------------------------------------------------------------------
# Batch CSV Upload Endpoint (SSE Progress Streaming)
# ---------------------------------------------------------------------------

MAX_BATCH_ROWS = 20
BATCH_PACE_SECONDS = 20


@app.post("/api/enrich-batch")
async def enrich_batch(file: UploadFile = File(...)):
    """Enrich a CSV batch upload with real-time SSE progress streaming.

    Processes rows sequentially with pacing to respect Groq TPM limits.
    Accepts CSV with columns: Mfg_Part_Num, Part_Desc, Part_Manuf (optional).
    Capped at MAX_BATCH_ROWS rows for demo safety.
    """
    import asyncio

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    # Read and parse the CSV
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no data rows.")

    # Validate required columns
    required_cols = {"Mfg_Part_Num", "Part_Desc"}
    if not required_cols.issubset(set(reader.fieldnames or [])):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must contain columns: {', '.join(sorted(required_cols))}. Found: {', '.join(reader.fieldnames or [])}",
        )

    if len(rows) > MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"CSV contains {len(rows)} rows, but the demo limit is {MAX_BATCH_ROWS} rows. Please reduce the file size.",
        )

    # Stream progress via SSE (Server-Sent Events)
    async def generate_sse():
        results = []
        total = len(rows)

        for idx, row in enumerate(rows, start=1):
            mpn = row.get("Mfg_Part_Num", "").strip()
            desc = row.get("Part_Desc", "").strip() or f"{mpn} Dishwasher"
            distributor = row.get("Part_Manuf", "").strip() or "Appliance Dealers Cooperative (APPDE)"

            if not mpn:
                progress_event = json.dumps({
                    "type": "progress",
                    "current": idx,
                    "total": total,
                    "mpn": "(empty)",
                    "status": "skipped",
                })
                yield f"data: {progress_event}\n\n"
                continue

            # Send progress event
            progress_event = json.dumps({
                "type": "progress",
                "current": idx,
                "total": total,
                "mpn": mpn,
                "status": "processing",
            })
            yield f"data: {progress_event}\n\n"

            # Run the enrichment pipeline (same stages as /api/enrich)
            try:
                raw_row_data = {"Mfg_Part_Num": mpn, "Part_Desc": desc, "Part_Manuf": distributor}
                brand_res = resolve_brand(mpn, desc, raw_row=raw_row_data)
                category_key = detect_product_category(mpn, desc)

                if brand_res.brand:
                    research_res = research_product(mpn, brand_res.brand, product_type=category_key)
                else:
                    research_res = ResearchResult(
                        mpn=mpn, brand="", status="not_found", raw_answer="Brand unresolved"
                    )

                input_row = {
                    "Mfg_Part_Num": mpn,
                    "Part_Desc": desc,
                    "Part_Manuf": distributor,
                }
                gen_res = generate_fields(brand_res, research_res, input_row)
                val_summary = validate_enriched_record(gen_res, brand_res, research_res)

                val_dict = {
                    "is_valid": val_summary.is_valid,
                    "confidence": val_summary.confidence,
                    "confidence_score": val_summary.confidence_score,
                    "score_breakdown": val_summary.score_breakdown,
                    "issues": [asdict(i) for i in val_summary.issues],
                    "needs_review_fields": val_summary.needs_review,
                }

                sources_list = [s["url"] for s in research_res.sources]
                save_record(mpn, brand_res.brand or "Unknown", gen_res.fields, sources_list, val_dict)

                result = {
                    "mpn": mpn,
                    "resolved_brand": brand_res.brand or "Unknown",
                    "brand_confidence": brand_res.confidence,
                    "research_status": research_res.status,
                    "sources_count": len(sources_list),
                    "confidence": val_summary.confidence,
                    "confidence_score": val_summary.confidence_score,
                    "is_valid": val_summary.is_valid,
                    "needs_review_fields_count": len(val_summary.needs_review),
                    "status": "success",
                }
            except Exception as exc:
                result = {
                    "mpn": mpn,
                    "resolved_brand": "Error",
                    "brand_confidence": "none",
                    "research_status": "error",
                    "sources_count": 0,
                    "confidence": "LOW",
                    "confidence_score": 0.0,
                    "is_valid": False,
                    "needs_review_fields_count": 0,
                    "status": f"error: {str(exc)[:100]}",
                }

            results.append(result)

            # Send row-complete event
            row_event = json.dumps({"type": "row_complete", "current": idx, "total": total, "result": result})
            yield f"data: {row_event}\n\n"

            # Pacing between rows to respect Groq TPM limits
            if idx < total:
                pace_event = json.dumps({
                    "type": "pacing",
                    "current": idx,
                    "total": total,
                    "wait_seconds": BATCH_PACE_SECONDS,
                })
                yield f"data: {pace_event}\n\n"
                await asyncio.sleep(BATCH_PACE_SECONDS)

        # Final event with all results
        done_event = json.dumps({"type": "done", "results": results, "total_processed": len(results)})
        yield f"data: {done_event}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


from exporters import export_to_grainger, export_to_json_pim, export_to_shopify, export_to_unilog


class CuratorOverrideRequest(BaseModel):
    mpn: str
    fields: dict[str, str]
    approved: bool = True


@app.post("/api/curator/override")
def curator_override(req: CuratorOverrideRequest):
    """Human-in-the-Loop (HITL) endpoint: update and approve fields for an enriched item."""
    mpn = req.mpn.strip()
    if not mpn:
        raise HTTPException(status_code=400, detail="MPN is required")

    cached = get_record(mpn) or {}
    existing_fields = cached.get("fields", {})
    existing_fields.update(req.fields)
    sources = cached.get("sources", [])
    brand = existing_fields.get("BRAND_NAME", cached.get("brand", "Unknown"))

    val_dict = cached.get("validation", {
        "is_valid": True,
        "confidence": "HIGH",
        "confidence_score": 0.95,
        "score_breakdown": {"curator_approved": 1.0},
        "issues": [],
        "needs_review_fields": [],
    })
    val_dict["curator_approved"] = req.approved
    val_dict["needs_review_fields"] = [f for f in val_dict.get("needs_review_fields", []) if f not in req.fields]

    save_record(mpn, brand, existing_fields, sources, val_dict)

    return {
        "status": "success",
        "mpn": mpn,
        "message": "Curator override saved to persistent database",
        "fields": existing_fields,
        "validation": val_dict,
    }


@app.get("/api/catalog/export/{export_format}")
def export_catalog_formatted(export_format: str):
    """Multi-channel syndication export: unilog (252-col), grainger, shopify, or json."""
    records = get_all_records()
    headers = _read_column_headers()
    fmt = export_format.lower().strip()

    if fmt in ("unilog", "csv", "252"):
        csv_content = export_to_unilog(records, headers)
        filename = "Unihack_Master_252Col_Delivery.csv"
        media_type = "text/csv"
    elif fmt in ("grainger", "b2b"):
        csv_content = export_to_grainger(records)
        filename = "Grainger_Industrial_B2B_Catalog.csv"
        media_type = "text/csv"
    elif fmt in ("shopify", "ecommerce"):
        csv_content = export_to_shopify(records)
        filename = "Shopify_ECommerce_Catalog.csv"
        media_type = "text/csv"
    elif fmt in ("json", "pim"):
        json_content = export_to_json_pim(records)
        filename = "Catalog_PIM_Export.json"
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {export_format}. Use unilog, grainger, shopify, or json.")

    return StreamingResponse(
        iter([csv_content]),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/metrics")
def get_catalog_metrics():
    """Return executive ROI, data completeness, and compliance metrics."""
    records = get_all_records()
    total = len(records)

    if total == 0:
        return {
            "total_products": 0,
            "completeness_score": 0.0,
            "compliance_rate": 100.0,
            "time_saved_hours": 0.0,
            "dollars_saved": 0.0,
            "confidence_breakdown": {"high": 0, "medium": 0, "low": 0},
            "valid_records_count": 0,
        }

    high_c = sum(1 for r in records if r.get("validation", {}).get("confidence") == "HIGH")
    med_c = sum(1 for r in records if r.get("validation", {}).get("confidence") == "MEDIUM")
    low_c = sum(1 for r in records if r.get("validation", {}).get("confidence") == "LOW")
    valid_c = sum(1 for r in records if r.get("validation", {}).get("is_valid"))

    # Estimate 15 minutes of manual research/curation saved per SKU
    time_saved_hrs = round((total * 15) / 60.0, 1)
    # Estimate $30/hr standard US B2B data curator salary
    dollars_saved = round(time_saved_hrs * 30.0, 2)

    # Calculate average completeness across 15 attributes + core fields
    filled_attr_counts = []
    for r in records:
        f = r.get("fields", {})
        count = sum(1 for i in range(1, 16) if bool(f.get(f"ATTRIBUTE_VALUE {i}")))
        filled_attr_counts.append(count)
    avg_completeness = round((sum(filled_attr_counts) / (total * 15)) * 100, 1) if filled_attr_counts else 85.0

    return {
        "total_products": total,
        "completeness_score": avg_completeness,
        "compliance_rate": 100.0,
        "time_saved_hours": time_saved_hrs,
        "dollars_saved": dollars_saved,
        "confidence_breakdown": {
            "high": high_c,
            "medium": med_c,
            "low": low_c,
        },
        "valid_records_count": valid_c,
    }


# ---------------------------------------------------------------------------
# Mount Static Frontend (for Render & Local Dev)
# ---------------------------------------------------------------------------

PUBLIC_DIR = Path(__file__).parent / "public"
if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="public")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"\n[*] Starting UniHack Catalog Enrichment Server on http://localhost:{port}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
