# Stage 3 — Field Generator

Takes brand_resolver output + web_research output (raw specs/sources) for one MPN and produces all 252 columns following the formulas reverse-engineered from the 2 ground-truth rows.

## Proposed Changes

### `src/field_generator.py` [NEW]

Single new file — `generate_fields(brand_resolution, research_result, input_row)` → `dict` with all 252 column keys.

#### Architecture (3-layer approach)

**Layer 1 — Deterministic fields (no LLM needed):**
Copy-through and constant fields that are always the same for dishwashers or come directly from input data:
- `Dept` = `"Appliances"`, `Class` = `"Large Appliances"`, `Fine` = `"Dishwashers"`
- `Classpath` = `"Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers"`
- `Product Name` = `"Dishwasher"`
- `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`, `Unilog_Brand`, `DIB_Brand`, `Part_Manuf` → copied from input row as-is
- `MANUFACTURER_PART_NUMBER` = MPN
- Attribute labels 1-15 are **fixed** (Series, Model, Wash Cycles, Voltage, Amperage, Mounting, Plug, Size, Depth, Min Height, Max Height, Sound Level, Material, Color, Additional Info)
- All remaining 252 columns initialized to `""` (empty string)

**Layer 2 — LLM extraction via Groq (`openai/gpt-oss-120b`, JSON mode):**
*Pre-check:* If `web_research.status == "not_found"`, skip the LLM call entirely to save tokens and mark all non-deterministic fields as `needs_review` with empty strings.

For found items, call the LLM with `temperature=0` (for strict reproducibility in extraction) and one structured prompt sending:
1. The raw research text from `web_research.py` (sources + raw_answer)
2. The exact field formulas from HANDOFF §4
3. Two ground-truth examples as few-shot references
4. Instruction to extract values **only from the provided research text** — no general knowledge

The LLM returns JSON with these keys:
```
manufacturer_name, brand_name, series, model, wash_cycles, voltage, voltage_uom,
amperage, amperage_uom, mounting_type, plug_type, size, depth_with_door_open,
depth_uom, min_height, min_height_uom, max_height, max_height_uom, sound_level,
sound_uom, material, color, additional_info, marketing_description,
item_features (list), with_text, standards_approvals, warranty
```

Each value includes a `_source` companion field (`"research"` | `"not_found"`) so we can trace provenance.

**Layer 3 — Formula assembly (deterministic, uses LLM output):**
Builds the final description fields from extracted values using dynamic priority-based joining:
- `MOBILE_DESC`: Join available fields (Manufacturer, Brand, Type, Series, MPN, Mounting) intelligently. Example: If Manufacturer and Brand differ significantly, join them space-separated; append available trailing specs like "Built-in Mounting". (60-80 char target).
- `INVOICE_DESC`: ALL CAPS, variable-length join in priority order (Type, Mounting, Cycles, Material, Voltage, Amperage, Depth OR Sound Level), skipping missing values to fit within ≤40 chars. Do NOT use a rigid template.
- `SHORT_DESC` = `"{Brand}® {Series} {MPN} Dishwasher, {feature/mounting}, {cycles}-Wash Cycle, {Material}"` (joining available fields)
- `LONG_DESC1` = Brand + Type + Series + full specs, ends with `"Additional Information: ..."`
- `RETAIL_DESC` = Series + Type + key specs (no brand, no MPN)
- Attribute values 1-15 slotted into fixed positions

#### Key Design Decisions

| Decision | Rationale |
|---|---|
| Read column headers from CSV at runtime | HANDOFF §4: "read them programmatically from the expected-output CSV, don't hardcode" |
| `json_object` mode (not `json_schema`) | Simpler, no schema enforcement headaches; we validate post-hoc |
| 2 ground-truth rows embedded in prompt as few-shot examples | Maximizes format accuracy — LLM sees the exact target format |
| Source tracking per field | HANDOFF: "Every generated field must be traceable" — each extracted value carries `_source` |
| Empty + `needs_review` for ungrounded fields | HANDOFF: "If the LLM can't ground a field in the research text, it must leave it empty and flag needs_review" |
| Deterministic description assembly (not LLM-generated) | Formulas are rigid string templates — letting the LLM freestyle them risks format drift |
| Abbreviation map for INVOICE_DESC | e.g. "Stainless Steel" → "SST", "Built-in" → "BLTLN", "Leg" → "LEG" — learned from ground truth |

#### Return Value

```python
@dataclass
class GenerationResult:
    mpn: str
    fields: dict[str, str]        # All 252 columns, keyed by exact CSV header
    field_sources: dict[str, str] # Which fields came from research vs constant vs empty
    needs_review: list[str]       # Field names that couldn't be grounded
    confidence: str               # "high" | "medium" | "low" based on grounding ratio
```

---

### Dependencies

- `groq` package (already in `requirements.txt`)
- `GROQ_API_KEY` (already in `.env`)
- No new dependencies needed

## Verification Plan

### Automated Tests
After building, I'll create a quick test script that:
1. Mocks the web_research output with the known specs for `PDSH4816AF` and `WDTS7024RZ`
2. Runs `generate_fields()` on both
3. Diffs the output against the ground-truth CSV field-by-field
4. Reports match % per field
5. **Automated Assertions**: Add strict assertions in the script to ensure `len(INVOICE_DESC) <= 40`, `60 <= len(MOBILE_DESC) <= 80` (or as close as possible via truncating/padding), and that attribute slots follow the exact fixed order.

### Manual Verification
- General review of formatting and readability of combined description fields
- Review handling of edge-case formats in the diff
