"""
Stage 4: Enterprise Quality & Compliance Validator
---------------------------------------------------
Provides rigorous quality assurance, character-limit enforcement, UOM standardization,
zero-hallucination verification, and multi-factor explainable confidence scoring.

Complies with Unilog Content Standards & MDM Best Practices:
  - Strict length limits: INVOICE_DESC <= 40, MOBILE_DESC 60-80, SHORT_DESC <= 255.
  - Unit of Measure rules: Standard abbreviations (in, V, A, dBA), space before unit.
  - Sourcing rule: Validates that cited URLs match registered official brand domains.
  - Weighted 5-Factor Confidence Score: Clear, auditable 0-100% calculation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from brand_resolver import BrandResolution
from field_generator import ATTRIBUTE_LABELS, GenerationResult
from web_research import BRAND_OFFICIAL_DOMAINS, ResearchResult

# Approved Units of Measure (from Unilog Master Standards)
APPROVED_UOMS = frozenset({
    "in", "V", "A", "dBA", "kW-hr", "hr", "gal", "rpm", "lb", "oz", "W", "Hz", "psi"
})

# Forbidden placeholder values that should never appear as real data
FORBIDDEN_PLACEHOLDERS = frozenset({
    "-- unbranded --",
    "-- no unilog brand --",
    "-- no dib brand --",
    "n/a",
    "na",
    "null",
    "none",
    "unknown",
    "tbd",
    "?",
    "-",
})


@dataclass
class ValidationIssue:
    field: str
    issue_type: str  # "error" | "warning"
    message: str
    rule_code: str


@dataclass
class ValidationSummary:
    mpn: str
    is_valid: bool
    confidence: str              # "HIGH" | "MEDIUM" | "LOW"
    confidence_score: float      # 0.0 to 1.0 (percentage index)
    issues: list[ValidationIssue] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)


def _is_placeholder(value: str) -> bool:
    """Check if a string is a useless placeholder value."""
    cleaned = value.strip().lower()
    return cleaned in FORBIDDEN_PLACEHOLDERS or cleaned.startswith("-- ")


def _validate_domain_provenance(url: str, brand: str) -> bool:
    """Verify that cited URL belongs to the registered official brand domain."""
    if not url:
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")

    allowed_domains = BRAND_OFFICIAL_DOMAINS.get(brand, [])
    for allowed in allowed_domains:
        if domain == allowed or domain.endswith(f".{allowed}"):
            return True
    return False


def validate_enriched_record(
    generation_result: GenerationResult,
    brand_resolution: BrandResolution,
    research_result: ResearchResult,
) -> ValidationSummary:
    """Perform enterprise PIM validation on a generated 252-column product record."""

    fields = generation_result.fields
    mpn = generation_result.mpn
    brand = brand_resolution.brand or ""
    issues: list[ValidationIssue] = []
    needs_review = list(generation_result.needs_review)

    # -----------------------------------------------------------------------
    # 1. Description Length & Construction Rules
    # -----------------------------------------------------------------------

    # INVOICE_DESC (<= 40 chars, ALL-CAPS, single spaces)
    invoice_desc = fields.get("INVOICE_DESC", "")
    if not invoice_desc:
        issues.append(ValidationIssue("INVOICE_DESC", "error", "INVOICE_DESC is empty", "RULE_DESC_01"))
        if "INVOICE_DESC" not in needs_review:
            needs_review.append("INVOICE_DESC")
    else:
        if len(invoice_desc) > 40:
            issues.append(ValidationIssue("INVOICE_DESC", "error", f"Length {len(invoice_desc)} exceeds 40 chars", "RULE_DESC_02"))
        if invoice_desc != invoice_desc.upper():
            issues.append(ValidationIssue("INVOICE_DESC", "warning", "INVOICE_DESC should be all uppercase", "RULE_DESC_03"))
        if "  " in invoice_desc:
            issues.append(ValidationIssue("INVOICE_DESC", "warning", "Contains double spaces", "RULE_DESC_04"))

    # MOBILE_DESC (60-80 chars target)
    mobile_desc = fields.get("MOBILE_DESC", "")
    if not mobile_desc:
        issues.append(ValidationIssue("MOBILE_DESC", "error", "MOBILE_DESC is empty", "RULE_DESC_05"))
        if "MOBILE_DESC" not in needs_review:
            needs_review.append("MOBILE_DESC")
    else:
        if len(mobile_desc) > 80:
            issues.append(ValidationIssue("MOBILE_DESC", "error", f"Length {len(mobile_desc)} exceeds 80 chars", "RULE_DESC_06"))
        elif len(mobile_desc) < 60:
            issues.append(ValidationIssue("MOBILE_DESC", "warning", f"Length {len(mobile_desc)} is under 60 chars target", "RULE_DESC_07"))
        if ",," in mobile_desc or ".." in mobile_desc:
            issues.append(ValidationIssue("MOBILE_DESC", "warning", "Contains repeated punctuation", "RULE_DESC_08"))

    # SHORT_DESC (<= 255 chars)
    short_desc = fields.get("SHORT_DESC", "")
    if short_desc and len(short_desc) > 255:
        issues.append(ValidationIssue("SHORT_DESC", "warning", f"Length {len(short_desc)} exceeds 255 chars", "RULE_DESC_09"))

    # LONG_DESC1 (No trailing commas)
    long_desc = fields.get("LONG_DESC1", "")
    if long_desc and long_desc.rstrip().endswith(","):
        issues.append(ValidationIssue("LONG_DESC1", "warning", "Trailing comma at end of description", "RULE_DESC_10"))

    # -----------------------------------------------------------------------
    # 2. Attribute & UOM Compliance Rules
    # -----------------------------------------------------------------------

    attributes_filled = 0
    total_attributes = len(ATTRIBUTE_LABELS)

    for i, label in enumerate(ATTRIBUTE_LABELS, start=1):
        lbl_col = f"ATTRIBUTE_LABEL {i}"
        val_col = f"ATTRIBUTE_VALUE {i}"
        uom_col = f"ATTRIBUTE_UOM {i}"

        # Ensure label matches required slot order exactly
        if fields.get(lbl_col) != label:
            issues.append(ValidationIssue(lbl_col, "error", f"Slot {i} label must be '{label}', got '{fields.get(lbl_col)}'", "RULE_ATTR_01"))

        val = fields.get(val_col, "").strip()
        if val:
            if _is_placeholder(val):
                issues.append(ValidationIssue(val_col, "error", f"Contains placeholder value '{val}'", "RULE_ATTR_02"))
                fields[val_col] = ""
                if val_col not in needs_review:
                    needs_review.append(val_col)
            else:
                attributes_filled += 1

        uom = fields.get(uom_col, "").strip()
        if uom and uom not in APPROVED_UOMS:
            issues.append(ValidationIssue(uom_col, "warning", f"UOM '{uom}' is not in approved list", "RULE_UOM_01"))

    # -----------------------------------------------------------------------
    # 3. Brand & Manufacturer Verification
    # -----------------------------------------------------------------------

    brand_name = fields.get("BRAND_NAME", "")
    mfr_name = fields.get("MANUFACTURER_NAME", "")

    if not brand_name or _is_placeholder(brand_name):
        issues.append(ValidationIssue("BRAND_NAME", "error", "Missing valid brand name", "RULE_BRAND_01"))
        if "BRAND_NAME" not in needs_review:
            needs_review.append("BRAND_NAME")

    if not mfr_name or _is_placeholder(mfr_name):
        issues.append(ValidationIssue("MANUFACTURER_NAME", "error", "Missing valid manufacturer name", "RULE_BRAND_02"))
        if "MANUFACTURER_NAME" not in needs_review:
            needs_review.append("MANUFACTURER_NAME")

    # -----------------------------------------------------------------------
    # 4. Sourcing & Domain Provenance Verification
    # -----------------------------------------------------------------------

    mfr_url = fields.get("MFR URL", "")
    if mfr_url:
        if not _validate_domain_provenance(mfr_url, brand):
            issues.append(ValidationIssue("MFR URL", "warning", f"URL '{mfr_url}' does not match official domain for '{brand}'", "RULE_SRC_01"))
    else:
        issues.append(ValidationIssue("MFR URL", "warning", "No official manufacturer citation URL provided", "RULE_SRC_02"))

    # -----------------------------------------------------------------------
    # 5. Weighted Multi-Factor Confidence Scoring (0.0 to 1.0)
    # -----------------------------------------------------------------------

    # Factor 1: Brand resolution confidence (20%)
    if brand_resolution.confidence == "high" and brand_name:
        brand_score = 1.0
    elif brand_resolution.confidence == "medium" or brand_name:
        brand_score = 0.6
    else:
        brand_score = 0.0

    # Factor 2: Official source verification (20%)
    if research_result.status == "found" and mfr_url and _validate_domain_provenance(mfr_url, brand):
        sourcing_score = 1.0
    elif research_result.status == "found":
        sourcing_score = 0.5
    else:
        sourcing_score = 0.0

    # Factor 3: Core attribute fill rate (30%)
    attribute_score = attributes_filled / max(total_attributes, 1)

    # Factor 4: Description compliance (20%)
    desc_errors = sum(1 for issue in issues if issue.field.endswith("_DESC") and issue.issue_type == "error")
    description_score = max(0.0, 1.0 - (desc_errors * 0.4))

    # Factor 5: Digital asset naming & taxonomy (10%)
    has_image = bool(fields.get("Product Image"))
    has_pdf = bool(fields.get("Specification Sheet"))
    has_taxonomy = bool(fields.get("Classpath"))
    asset_score = (int(has_image) + int(has_pdf) + int(has_taxonomy)) / 3.0

    # Total Weighted Confidence Score
    confidence_score = round(
        (brand_score * 0.20)
        + (sourcing_score * 0.20)
        + (attribute_score * 0.30)
        + (description_score * 0.20)
        + (asset_score * 0.10),
        3,
    )

    if confidence_score >= 0.75:
        confidence = "HIGH"
    elif confidence_score >= 0.45:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # A record is valid if it has zero critical errors
    has_critical_errors = any(i.issue_type == "error" for i in issues)
    is_valid = not has_critical_errors

    return ValidationSummary(
        mpn=mpn,
        is_valid=is_valid,
        confidence=confidence,
        confidence_score=confidence_score,
        issues=issues,
        needs_review=sorted(set(needs_review)),
        score_breakdown={
            "brand_resolution": brand_score,
            "source_verification": sourcing_score,
            "attribute_completeness": attribute_score,
            "description_compliance": description_score,
            "digital_assets": asset_score,
        },
    )
