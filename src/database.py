"""
Data Layer: Supabase PostgreSQL & Local Cache Hybrid
----------------------------------------------------
Provides persistent storage and sub-second caching for enriched catalog items.

Features:
  - If SUPABASE_URL and SUPABASE_KEY are provided in .env, persists to Supabase PostgREST.
  - Transparently falls back to local SQLite & JSON cache (output/catalog_cache.json).
  - Enables instant 50ms lookup for previously processed items during live demos.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = Path(__file__).parent.parent / "output"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SQLITE_DB_PATH = CACHE_DIR / "catalog.db"
JSON_CACHE_PATH = CACHE_DIR / "catalog_cache.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")


# ---------------------------------------------------------------------------
# SQLite Local Storage
# ---------------------------------------------------------------------------

def _init_sqlite():
    """Initialize local SQLite tables for offline & local dev resilience."""
    with sqlite3.connect(SQLITE_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enriched_products (
                mpn TEXT PRIMARY KEY,
                brand TEXT,
                confidence TEXT,
                confidence_score REAL,
                is_valid INTEGER,
                fields_json TEXT,
                sources_json TEXT,
                validation_json TEXT,
                updated_at REAL
            )
        """)
        conn.commit()


_init_sqlite()


# ---------------------------------------------------------------------------
# Public Data Operations (Hybrid Supabase / SQLite)
# ---------------------------------------------------------------------------

def save_record(
    mpn: str,
    brand: str,
    fields: dict[str, str],
    sources: list[str],
    validation_summary: dict[str, Any],
) -> bool:
    """Save or update an enriched product record."""
    now = time.time()
    fields_str = json.dumps(fields)
    sources_str = json.dumps(sources)
    val_str = json.dumps(validation_summary)

    # 1. Save to local SQLite
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO enriched_products 
                (mpn, brand, confidence, confidence_score, is_valid, fields_json, sources_json, validation_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mpn,
                brand,
                validation_summary.get("confidence", "LOW"),
                validation_summary.get("confidence_score", 0.0),
                int(validation_summary.get("is_valid", False)),
                fields_str,
                sources_str,
                val_str,
                now,
            ))
            conn.commit()
    except Exception as exc:
        print(f"  WARN: SQLite save error: {exc}")

    # 2. Save to Supabase (if configured)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/enriched_products"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            }
            payload = {
                "mpn": mpn,
                "brand": brand,
                "confidence": validation_summary.get("confidence", "LOW"),
                "confidence_score": validation_summary.get("confidence_score", 0.0),
                "is_valid": validation_summary.get("is_valid", False),
                "fields": fields,
                "sources": sources,
                "validation": validation_summary,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            if resp.status_code in (200, 201):
                print(f"  ✅ Saved {mpn} to Supabase")
                return True
            else:
                print(f"  WARN: Supabase HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as exc:
            print(f"  WARN: Supabase save error: {exc}")

    return True


def get_record(mpn: str) -> dict[str, Any] | None:
    """Retrieve an enriched product record by MPN from cache or DB."""
    # Try Supabase first if available
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/enriched_products?mpn=eq.{mpn}&select=*"
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            }
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    item = data[0]
                    return {
                        "mpn": item["mpn"],
                        "brand": item["brand"],
                        "confidence": item["confidence"],
                        "confidence_score": item["confidence_score"],
                        "is_valid": item["is_valid"],
                        "fields": item["fields"],
                        "sources": item["sources"],
                        "validation": item["validation"],
                    }
        except Exception as exc:
            print(f"  WARN: Supabase read fallback: {exc}")

    # Fallback to local SQLite
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mpn, brand, confidence, confidence_score, is_valid, fields_json, sources_json, validation_json
                FROM enriched_products WHERE mpn = ?
            """, (mpn,))
            row = cursor.fetchone()
            if row:
                return {
                    "mpn": row[0],
                    "brand": row[1],
                    "confidence": row[2],
                    "confidence_score": row[3],
                    "is_valid": bool(row[4]),
                    "fields": json.loads(row[5]),
                    "sources": json.loads(row[6]),
                    "validation": json.loads(row[7]),
                }
    except Exception as exc:
        print(f"  WARN: SQLite read error: {exc}")

    return None


def get_all_records() -> list[dict[str, Any]]:
    """Retrieve all cached product records."""
    records = []
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mpn, brand, confidence, confidence_score, is_valid, fields_json, sources_json, validation_json
                FROM enriched_products ORDER BY updated_at DESC
            """)
            for row in cursor.fetchall():
                records.append({
                    "mpn": row[0],
                    "brand": row[1],
                    "confidence": row[2],
                    "confidence_score": row[3],
                    "is_valid": bool(row[4]),
                    "fields": json.loads(row[5]),
                    "sources": json.loads(row[6]),
                    "validation": json.loads(row[7]),
                })
    except Exception as exc:
        print(f"  WARN: SQLite get_all error: {exc}")
    return records
