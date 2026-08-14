# PROJECT HANDOFF — UniHack Product Intelligence Pipeline

> This file is for whichever AI/dev picks up this codebase next (currently:
> Opus inside Antigravity). Read this fully before writing any code. It
> contains the full context, decisions already made (with reasoning), what's
> built, what's next, and hard constraints that must not be violated.

---

## 1. END GOAL

Build an AI pipeline that converts messy, minimal product data (a part number
+ a cryptic 3-6 word description) into a rich, standardized, commerce-ready
product record matching a strict 252-column schema. This is for the UniHack
hackathon (Hack2skill x Unilog), **deadline 23 Aug 2026, 11:59 PM IST**.

**Win condition (per organizer's own guidance):** depth beats breadth. Judges
explicitly said don't try to enrich all 1000 sample rows shallowly — pick one
narrow category, solve it with high accuracy, and prove it with evaluation
metrics (field-level accuracy %, char-limit compliance, source citations).

## 2. SCOPE DECISION (already made, do not second-guess without new evidence)

**Category locked: Dishwashers**, specifically the 10 dishwasher items inside
the "Appliance Dealers Cooperative (APPDE)" manufacturer cluster in
`data/Unihack__Sample_Dataset_-_Input.csv`.

**Why:** The only 2 ground-truth example rows we were given
(`data/Unihack__Expected_Output_-_Delivery_Format.csv`) are BOTH dishwashers
from this exact APPDE cluster (MPNs `PDSH4816AF` and `WDTS7024RZ`). This is
not a coincidence — it's the organizer showing us exactly what a correct
answer looks like for this specific sub-population. Reverse-engineering the
schema from these 2 rows is our primary source of truth, more reliable than
the general Solution Guide text.

**Stretch goal (only after dishwashers are solid):** extend to the rest of
the 84-item APPDE cluster (dryers, washers, ranges, microwaves, etc.) — same
manufacturer family, so the manufacturer-resolution and research approach
transfers directly.

## 3. CRITICAL FACTS DISCOVERED (do not re-derive, just use)

- **`Part_Manuf` in raw input is the DISTRIBUTOR, not the real manufacturer.**
  Example: raw `Part_Manuf` = "Appliance Dealers Cooperative (APPDE)", but the
  ground-truth `MANUFACTURER_NAME` for that row is "Rheem Manufacturing" and
  `BRAND_NAME` is "Frigidaire®". Never map `Part_Manuf` straight into any
  output field.
- **Expected output has 252 columns**, only 2 are filled as ground truth. No
  other reference files (no LOV lists, no manufacturer master list, no UOM
  standards doc) were actually provided on the dashboard — the Solution Guide
  text mentions files that do not exist in our resources. Work only with the
  3 real files: Solution Guide (text/context only), Sample Input (1000 rows),
  Expected Output (252-col schema + 2 example rows).
- **Sourcing rule (hackathon rule, non-negotiable):** product specs must come
  from the manufacturer's OWN official website only. Marketplace/distributor
  sites are disallowed. This is enforced in code via `include_domains` in the
  Tavily search — do not remove this restriction to "get more results."
- **Missing/uncertain data must be flagged, not invented.** A `confidence`
  field and `needs_review` status are treated as a FEATURE by the judges, not
  a weakness. Never silently fabricate a spec value.

## 4. FIELD FORMULAS (reverse-engineered from the 2 ground-truth rows)

| Field | Formula |
|---|---|
| PART_NUMBER / SKU | System-assigned random ID — don't generate, leave for the submission system |
| Dept / Class / Fine | Fixed constants for this category: `Appliances / Large Appliances / Dishwashers` |
| MANUFACTURER_NAME | Real legal company name, from web research (NOT `Part_Manuf`) |
| BRAND_NAME | Trademark-symbol form, e.g. `Frigidaire®` |
| Classpath | Fixed: `Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers` |
| MOBILE_DESC (60-80 char) | `{Manufacturer}, {Brand}, Dishwasher, {Series}, {MPN}` |
| INVOICE_DESC (≤40 char, ALL CAPS) | `DISHWASHER {mounting} {cycles} {material-abbr} {voltage}V {amp}A {depth}IN` |
| SHORT_DESC / Title | `{Brand}® {Series} {MPN} Dishwasher, {feature/mounting}, {cycles}-Wash Cycle, {Material}` |
| LONG_DESC1 | Brand + Type + Series + full comma-separated specs, ends with "Additional Information: ..." |
| RETAIL_DESC | Series + Type + key specs, no brand, no MPN (shorter marketing copy) |
| Attributes 1-15 | FIXED SLOT ORDER (same every row): Series → Model → Wash Cycles → Voltage → Amperage → Mounting → Plug → Size → Depth → Min Height → Max Height → Sound Level → Material → Color → Additional Info. Leave a slot **empty** if data is genuinely unavailable — never skip/reorder slots. |

Exact column headers are in `data/Unihack__Expected_Output_-_Delivery_Format.csv`
row 1 — always read from that file, don't hardcode header names elsewhere.

## 5. WHAT'S ALREADY BUILT (tested, working)

- `src/brand_resolver.py` — **Stage 1, done.** Resolves real brand per row
  using (a) explicit brand keyword in `Part_Desc` text [high confidence], (b)
  known MPN-prefix pattern [medium confidence, needs Stage 2 verification].
  Tested: 10/10 dishwashers resolved, both ground-truth rows matched
  correctly. Never returns a guessed brand silently — unmatched rows get
  `confidence="needs_review"`.

- `src/web_research.py` — **Stage 2, code complete, untested with a real key.**
  Uses Tavily search restricted via `include_domains` to the resolved brand's
  official domain only (see `BRAND_OFFICIAL_DOMAINS` dict — extend this as
  new brands come up, don't remove entries). Returns `status="not_found"`
  rather than silently falling back to unrestricted search if nothing turns
  up on the official domain — this is intentional, keep it.

- `data/` — both source CSVs, unmodified.

## 6. WHAT'S NOT BUILT YET (the actual next work)

### Stage 3 — Field Generator (build this next)
Takes: brand_resolver output + web_research output (raw specs text/sources)
for one MPN.
Produces: all 252 columns for that row, following the formulas in section 4.
Approach: Groq LLM (`openai/gpt-oss-120b`, free tier) with a **strict
structured prompt** — feed it the exact formulas above plus the raw research
text, force JSON output matching the real column headers (read them
programmatically from the expected-output CSV, don't hardcode). Every
generated field must be traceable to either (a) the research source text, or
(b) a fixed constant from section 4 — no field should come from the LLM's
general/parametric knowledge about dishwashers, because that can't be
sourced back to a manufacturer's site and will fail the sourcing-rule
evaluation. If the LLM can't ground a field in the research text, it must
leave it empty and flag `needs_review`, not invent a plausible-sounding value.

### Stage 4 — Validator
Checks per generated row:
- Char limits (e.g. INVOICE_DESC ≤ 40 chars, MOBILE_DESC 60-80 chars)
- No placeholder junk ("-- Unbranded --", "N/A" etc.) written into real fields
- Confidence score per row (aggregate of brand_resolver confidence + how many
  fields came from real research vs were left empty)
- Cross-check that any cited source URL is actually on the brand's official
  domain (defense in depth, in case Stage 3 prompt leaks around the rule)

### Stage 5 — Runner / orchestration script
One script that loops over the 10 dishwasher rows, runs all 4 stages, writes
results to `output/`, and prints a summary table (accuracy against the 2
ground-truth rows, confidence distribution, needs_review count).

### Stage 6 — Demo/submission polish (later, not urgent yet)
- Simple before/after view (raw row → enriched row) for the demo video
- Deploy prototype (Render/Vercel, same as prior projects)
- Prototype deck (mandatory template), Solution Brief, GitHub repo cleanup

## 7. USE / AVOID

**Use:**
- Groq (`openai/gpt-oss-120b`) for generation — free tier
- Tavily for research — free tier, domain-restricted
- Python stdlib `csv`/`dataclasses` — keep dependencies minimal
- The 2 ground-truth rows as the automated accuracy check for every pipeline
  change — re-run against them after any Stage 3/4 edit

**Avoid:**
- Do NOT map `Part_Manuf` (distributor) directly to `MANUFACTURER_NAME` or
  `BRAND_NAME`
- Do NOT remove the `include_domains` restriction in `web_research.py` to
  "improve recall" — sourcing-rule violation risk is worse than lower recall
- Do NOT let the LLM in Stage 3 fill fields from general world knowledge
  ungrounded in the fetched research — un-sourceable data will hurt accuracy
  scoring
- Do NOT silently skip or reorder the Attributes 1-15 slots
- Do NOT try to cover all 1000 rows / all categories — that's explicitly the
  losing strategy per the organizer's own guidance
- Do NOT commit a real `.env` file with API keys to GitHub — only
  `.env.example`
- No temporary/band-aid fixes — if a pipeline stage produces a wrong field,
  find why (bad prompt, missing research grounding, wrong formula) and fix
  the root cause, not just that one row's output

## 8. HOW TO VALIDATE PROGRESS

After building Stage 3+4, run the full pipeline on `PDSH4816AF` and
`WDTS7024RZ` (the 2 ground-truth MPNs) and diff the output against
`data/Unihack__Expected_Output_-_Delivery_Format.csv` field-by-field. This is
the single most important test — if these 2 rows don't match closely, nothing
else matters yet. Only after these pass should you run the other 8
dishwashers.
