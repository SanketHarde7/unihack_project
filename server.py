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
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from brand_resolver import resolve_brand
from database import get_all_records, get_record, save_record
from field_generator import _read_column_headers, generate_fields
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

    # 3. Stage 2: Web Research
    if brand_res.brand:
        research_res = research_product(mpn, brand_res.brand, product_type="dishwasher")
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


@app.get("/api/catalog")
def get_catalog():
    """Return all enriched products currently in the database."""
    records = get_all_records()
    return {
        "total": len(records),
        "items": records,
    }


@app.get("/api/catalog/export")
def export_catalog_csv():
    """Stream download the full 252-column delivery CSV."""
    headers = _read_column_headers()
    records = get_all_records()

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()

    for item in records:
        fields = item.get("fields", {})
        ordered_row = {h: fields.get(h, "") for h in headers}
        writer.writerow(ordered_row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=Unihack_Enriched_Catalog_Delivery.csv"},
    )


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
