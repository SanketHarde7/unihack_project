# UniHack — Product Intelligence Pipeline (Dishwasher Deep-Dive)

Scope: 10 dishwashers from the "Appliance Dealers Cooperative (APPDE)" cluster
in the sample dataset — chosen because the 2 provided ground-truth examples
are both from this exact cluster.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your real GROQ_API_KEY and TAVILY_API_KEY
```

## Project structure

```
unihack/
├── data/
│   ├── Unihack__Sample_Dataset_-_Input.csv        (1000 raw rows)
│   └── Unihack__Expected_Output_-_Delivery_Format.csv  (252-col ground truth, 2 rows)
├── src/
│   ├── brand_resolver.py   (Stage 1 - DONE, tested, 10/10 dishwashers resolved)
│   └── web_research.py     (Stage 2 - DONE, needs TAVILY_API_KEY to run for real)
├── output/                 (Stage 3+ generated results go here)
├── requirements.txt
└── .env.example
```

## Pipeline stages

1. **Brand Resolver** (`src/brand_resolver.py`) — resolves real brand from
   Part_Desc text match or MPN-prefix pattern. Never guesses silently;
   unresolved rows are flagged `needs_review`.
   Run: `python3 src/brand_resolver.py`

2. **Web Research Agent** (`src/web_research.py`) — searches the resolved
   brand's OFFICIAL domain only (sourcing-rule compliant) via Tavily for
   real specs. Returns `not_found` rather than falling back to unrestricted
   search, to protect accuracy.
   Run: `python3 src/web_research.py` (requires `TAVILY_API_KEY` in `.env`)

3. **Field Generator** — not yet built. Will take resolver output +
   research output and populate the 252-column schema using the formulas
   reverse-engineered from ground truth (see chat history / next steps).

4. **Validator** — not yet built. Will check char limits, placeholder
   filtering, and attach confidence/needs-review flags.

## Key finding baked into this code

`Part_Manuf` in the raw data is the **distributor**, not the real
manufacturer (confirmed from ground truth: distributor = "Appliance Dealers
Cooperative", but real `MANUFACTURER_NAME` = "Rheem Manufacturing" /
"Whirlpool Corporation"). Never map `Part_Manuf` directly to output fields.
