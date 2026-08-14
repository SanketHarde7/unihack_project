# 🏆 UniHack EnrichAI — Autonomous Enterprise Product Catalog Enrichment Engine

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**UniHack 2026 Submission** — An autonomous, multi-tier AI enrichment platform that transforms messy, cryptic raw distributor rows into structured, verified, 252-column commerce-ready product master records with **zero hallucination** and **complete explainability**.

---

## 🏛️ System Architecture

```
[Raw Distributor Stream] (Mfg_Part_Num, Part_Desc, Brand Placeholders)
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: Brand & Entity Resolver (src/brand_resolver.py)    │
│ • Deterministic MPN prefix parsing & text cues              │
│ • Canonical Brand Mapping with ® / ™ symbols                │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: Smart Web Research Agent (src/web_research.py)     │
│ • Domain-locked multi-query search (specs, install, pdf)    │
│ • Anti-Noise Filter (rejects /support/, /blog/, etc.)       │
│ • Full-page HTML/PDF fetch with 403 Bot-Block fallback      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 3: 252-Column Field Generator (src/field_generator.py)│
│ • Layer 1: Deterministic taxonomy & input copy-through      │
│ • Layer 2: Zero-temp LLM structured extraction (Groq)       │
│ • Layer 3: Dynamic variable-length description builders     │
│ • Canonical post-normalization (® symbols, series suffix)   │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 4: Enterprise PIM Validator (src/validator.py)        │
│ • Strict Rule Engine (INVOICE ≤40 chars, MOBILE 60-80 chars)│
│ • Sourcing & Domain Provenance Verification                 │
│ • Explainable 5-Factor Weighted Confidence Scoring (0-100%) │
│ • Automated "needs_review" Field-Level Flagging             │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 5: Batch Runner & API Server (server.py)              │
│ • High-performance FastAPI backend (deployable on Render)   │
│ • Glassmorphic Dark UI (deployable on Vercel)               │
│ • Supabase PostgreSQL & SQLite Hybrid Persistence           │
│ • Output: 252-Column Final CSV & JSON Audit Trail Report    │
└─────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Capabilities & Evaluation Pillars

1. **Understanding Limited Info (Pillar 1):** Resolves ambiguous or unbranded inputs (e.g. `PDSH4816AF` ➔ `FRIGIDAIRE®` and `Rheem Manufacturing`).
2. **Sourcing Rule Compliance (Pillar 2):** Researches **strictly on official manufacturer domains** (e.g. `frigidaire.com`, `whirlpool.com`). Excludes third-party marketplaces and blogs.
3. **Commerce-Ready Delivery (Pillar 3):** Populates all 252 static delivery columns, formatting 5 distinct descriptions (`INVOICE_DESC`, `MOBILE_DESC`, `SHORT_DESC`, `LONG_DESC1`, `RETAIL_DESC`) and 15 standardized technical attribute slots with units.
4. **Zero-Hallucination & Explainability (Pillar 4):** Sourced specs are tagged `[Research]`; unverified fields are left blank and routed to `needs_review`. Generates itemized 5-factor confidence scores.
5. **Production Scalability (Pillar 5):** Multi-tier cloud architecture with sub-second database caching, rate-limit pacing, and batch export.

---

## 🚀 Quick Start (Local Setup)

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/SanketHarde7/unihack_project.git
cd unihack_project
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create a `.env` file in the root directory:
```env
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key

# Optional (for Supabase PostgreSQL persistence)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
```

### 3. Run Web Dashboard & API Server
```bash
python server.py
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser to interact with the dashboard.

---

## ☁️ 1-Click Cloud Deployment

* **Backend on Render:** Connect repo to Render ➔ Web Service ➔ Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
* **Frontend on Vercel:** Import repo on Vercel (zero-config, uses `vercel.json`).
* **Database on Supabase:** PostgreSQL table `enriched_products` stores cached master records.

---

## 📁 Repository Structure

```
unihack/
├── data/
│   ├── Unihack__Sample_Dataset_-_Input.csv        (1000 raw input rows)
│   └── Unihack__Expected_Output_-_Delivery_Format.csv (252-column ground truth template)
├── src/
│   ├── brand_resolver.py   (Stage 1: Regex & prefix entity resolution)
│   ├── web_research.py     (Stage 2: Anti-noise domain-locked web research)
│   ├── field_generator.py  (Stage 3: 3-Layer 252-column field generation)
│   ├── validator.py        (Stage 4: Quality rules & 5-factor confidence scoring)
│   ├── pipeline_runner.py  (Stage 5: Batch processor & exporter)
│   └── database.py         (Hybrid Supabase & SQLite persistence layer)
├── public/                 (Glassmorphic Dark UI: HTML5, CSS3, ES6)
├── output/                 (Generated CSV & audit reports)
├── server.py               (FastAPI production server)
├── render.yaml             (Render deployment blueprint)
├── Procfile                (PaaS start command)
├── vercel.json             (Vercel routing configuration)
└── requirements.txt        (Python dependencies)
```
