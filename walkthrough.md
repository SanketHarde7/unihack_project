# Stage 3 — Field Generator: Build Walkthrough

## What was built

### [field_generator.py](file:///c:/Users/sanke/OneDrive/Desktop/unihack/src/field_generator.py) `[NEW]`

3-layer architecture that converts brand_resolver + web_research output into all 252 CSV columns:

| Layer | What it does | LLM needed? |
|---|---|---|
| **Layer 1** | Fills constants (`Dept`, `Class`, `Classpath`…), copies input row fields, sets 15 fixed attribute labels | No |
| **Layer 2** | Calls Groq `openai/gpt-oss-120b` (temperature=0, JSON mode) to extract raw specs from research text. Skips LLM entirely if research `status != "found"` | Yes |
| **Layer 3** | Assembles MOBILE_DESC, INVOICE_DESC, SHORT_DESC, LONG_DESC1, RETAIL_DESC from extracted specs using dynamic priority-based joining | No |

**Key design decisions:**
- **INVOICE_DESC**: Variable-length greedy join (not rigid template). Adds specs in priority order, skips any that would exceed 40 chars. Depth and sound level compete for the last slot.
- **MOBILE_DESC**: Manufacturer+Brand space-joined if different entities, else brand only. Appends mounting if under 60 chars to reach 60-80 target.
- **With-text heuristic**: Single-feature names (no comma) are embedded in descriptions; feature lists are kept in the `With` column only.
- **LONG_DESC1**: Per-attribute formatters handle varying UOM patterns (embedded vs separate).

### Files modified

| File | Change |
|---|---|
| [brand_resolver.py](file:///c:/Users/sanke/OneDrive/Desktop/unihack/src/brand_resolver.py) | Added `from __future__ import annotations` for Python 3.9 compat |

### Test file

| File | Purpose |
|---|---|
| [test_field_generator.py](file:///c:/Users/sanke/OneDrive/Desktop/unihack/test_field_generator.py) | Ground-truth verification + char-limit assertions |

## Test results

```
TESTING DESCRIPTION BUILDERS (deterministic, no LLM)
--- PDSH4816AF ---
  ✅ MOBILE_DESC  (len=75)     ← within 60-80 ✓
  ✅ INVOICE_DESC (len=38)     ← within ≤40 ✓
  ✅ SHORT_DESC
  ✅ LONG_DESC1   (len=390)
  ✅ RETAIL_DESC

--- WDTS7024RZ ---
  ✅ MOBILE_DESC  (len=64)     ← within 60-80 ✓
  ✅ INVOICE_DESC (len=39)     ← within ≤40 ✓
  ✅ SHORT_DESC
  ✅ LONG_DESC1   (len=405)
  ✅ RETAIL_DESC

TESTING ATTRIBUTE SLOT ORDERING
  ✅ All 15 labels match for both rows

TESTING FULL GENERATE (mock LLM)
  PDSH4816AF: 13/13 fields match (100%)
  WDTS7024RZ: 13/13 fields match (100%)

✅ ALL TESTS PASSED
```

## What's next

- **Stage 4 — Validator**: Char-limit checks, placeholder detection, confidence scoring, source-domain verification
- **Stage 5 — Runner**: Orchestration script looping over 10 dishwashers, writing CSV to `output/`
- To run with real API: set `GROQ_API_KEY` and `TAVILY_API_KEY` in `.env`, then use Stage 5's runner
