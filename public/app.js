/**
 * UniHack EnrichAI — Frontend Application Controller
 * Handles live 5-stage stepper animations, interactive tab switching,
 * circular progress gauge rendering, and 252-column master data table updates.
 */

const API_BASE = window.location.origin;

// State
let currentRecord = null;
let currentTab = "invoice";

// DOM Elements
const enrichForm = document.getElementById("enrich-form");
const enrichBtn = document.getElementById("enrich-btn");
const btnText = enrichBtn.querySelector(".btn-text");
const btnLoader = enrichBtn.querySelector(".btn-loader");
const mpnInput = document.getElementById("mpn-input");
const descInput = document.getElementById("desc-input");
const distributorInput = document.getElementById("distributor-input");
const refreshCheckbox = document.getElementById("refresh-checkbox");
const presetButtons = document.querySelectorAll(".preset-btn");

// Results Elements
const confBadge = document.getElementById("conf-badge");
const progressCircle = document.getElementById("progress-circle");
const scoreText = document.getElementById("score-text");
const cacheBadge = document.getElementById("cache-badge");
const entityBrand = document.getElementById("entity-brand");
const entityMfr = document.getElementById("entity-mfr");
const entityClasspath = document.getElementById("entity-classpath");
const entityUrl = document.getElementById("entity-url");
const entityUrlText = document.getElementById("entity-url-text");

// Description Elements
const descTabs = document.querySelectorAll(".desc-tab");
const descGuideline = document.getElementById("desc-guideline");
const charPill = document.getElementById("char-pill");
const descTextOutput = document.getElementById("desc-text-output");

// Grid & Table Elements
const attributesGrid = document.getElementById("attributes-grid");
const masterTableBody = document.getElementById("master-table-body");
const downloadCsvBtn = document.getElementById("download-csv-btn");
const downloadJsonBtn = document.getElementById("download-json-btn");

// Description Metadata & Guidelines
const DESC_CONFIGS = {
  invoice: {
    field: "INVOICE_DESC",
    label: "INVOICE_DESC (Till Receipt / POS)",
    guideline: "Constraint: ≤ 40 characters, ALL-CAPS greedy priority-join of available specs.",
    maxChars: 40,
    checkType: "max",
  },
  mobile: {
    field: "MOBILE_DESC",
    label: "MOBILE_DESC (E-Commerce App)",
    guideline: "Constraint: 60–80 characters, Brand + Category + Series + MPN + Trailing spec.",
    maxChars: 80,
    minChars: 60,
    checkType: "range",
  },
  short: {
    field: "SHORT_DESC",
    label: "SHORT_DESC (Search Result Snippet)",
    guideline: "Constraint: ≤ 255 characters, Opening title + trailing comma-separated key attributes.",
    maxChars: 255,
    checkType: "max",
  },
  long: {
    field: "LONG_DESC1",
    label: "LONG_DESC1 (Product Detail Page)",
    guideline: "Constraint: Join of all 15 attribute values in slot order with standard units.",
    maxChars: 1000,
    checkType: "none",
  },
  retail: {
    field: "RETAIL_DESC",
    label: "RETAIL_DESC (Store Shelf Tag)",
    guideline: "Constraint: Series + Item Type + Key specs (No brand or MPN).",
    maxChars: 255,
    checkType: "max",
  },
};

// 15 Fixed Attribute Labels
const ATTRIBUTE_LABELS = [
  "Series", "Model", "Number of Wash Cycles", "Voltage Rating", "Amperage Rating",
  "Mounting Type", "Plug Type", "Size", "Depth With Door Open", "Minimum Height",
  "Maximum Height", "Sound Level", "Material", "Color", "Additional Information",
];


// ---------------------------------------------------------------------------
// Pipeline Stepper Controller
// ---------------------------------------------------------------------------

function resetStepper() {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step-${i}`);
    el.className = "step-item";
    el.querySelector(".step-status").innerHTML = '<i class="fa-solid fa-clock"></i>';
  }
}

function setStepActive(stepNum) {
  for (let i = 1; i < stepNum; i++) {
    const prev = document.getElementById(`step-${i}`);
    prev.className = "step-item completed";
    prev.querySelector(".step-status").innerHTML = '<i class="fa-solid fa-check"></i>';
  }
  const current = document.getElementById(`step-${stepNum}`);
  if (current) {
    current.className = "step-item active";
    current.querySelector(".step-status").innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
  }
}

function setAllStepsCompleted() {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById(`step-${i}`);
    el.className = "step-item completed";
    el.querySelector(".step-status").innerHTML = '<i class="fa-solid fa-check"></i>';
  }
}


// ---------------------------------------------------------------------------
// API Enrichment Call
// ---------------------------------------------------------------------------

async function enrichProduct(mpn, description, distributor, forceRefresh = false) {
  resetStepper();
  btnText.classList.add("hidden");
  btnLoader.classList.remove("hidden");
  enrichBtn.disabled = true;

  // Animate initial step
  setStepActive(1);

  try {
    // Step 2 transition animation
    setTimeout(() => setStepActive(2), 600);
    setTimeout(() => setStepActive(3), 1400);

    const response = await fetch(`${API_BASE}/api/enrich`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mpn: mpn.trim(),
        description: description.trim(),
        distributor: distributor.trim(),
        force_refresh: forceRefresh,
      }),
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Enrichment failed");
    }

    const data = await response.json();
    currentRecord = data;

    // Finish stepper animation
    setStepActive(4);
    setTimeout(() => {
      setAllStepsCompleted();
      renderRecord(data);
    }, 400);

  } catch (err) {
    alert(`Error: ${err.message}`);
    resetStepper();
  } finally {
    btnText.classList.remove("hidden");
    btnLoader.classList.add("hidden");
    enrichBtn.disabled = false;
  }
}


// ---------------------------------------------------------------------------
// Render Enriched Output
// ---------------------------------------------------------------------------

function renderRecord(data) {
  const fields = data.fields || {};
  const validation = data.validation || {};
  const fieldSources = data.field_sources || {};
  const breakdown = validation.score_breakdown || {};

  // 1. Confidence & Quality Score
  const score = validation.confidence_score !== undefined ? validation.confidence_score : 0.65;
  const scorePercent = Math.round(score * 100);
  const confidence = validation.confidence || "MEDIUM";

  scoreText.textContent = `${scorePercent}%`;
  progressCircle.style.background = `conic-gradient(var(--accent-cyan) ${scorePercent * 3.6}deg, rgba(51, 65, 85, 0.4) 0deg)`;

  confBadge.textContent = confidence;
  confBadge.className = `conf-badge badge-${confidence.toLowerCase()}`;

  // Breakdown bars
  document.getElementById("bar-brand").style.width = `${(breakdown.brand_resolution || 0.6) * 100}%`;
  document.getElementById("bar-source").style.width = `${(breakdown.source_verification || 1.0) * 100}%`;
  document.getElementById("bar-attr").style.width = `${(breakdown.attribute_completeness || 0.5) * 100}%`;
  document.getElementById("bar-desc").style.width = `${(breakdown.description_compliance || 1.0) * 100}%`;
  document.getElementById("bar-asset").style.width = `${(breakdown.digital_assets || 1.0) * 100}%`;

  // 2. Canonical Identity Master
  cacheBadge.innerHTML = data.cached
    ? '<i class="fa-solid fa-bolt"></i> Instant Cache'
    : '<i class="fa-solid fa-satellite-dish"></i> Live Sourced';

  entityBrand.textContent = fields["BRAND_NAME"] || data.brand || "Unresolved";
  entityMfr.textContent = fields["MANUFACTURER_NAME"] || "Unresolved";
  entityClasspath.textContent = fields["Classpath"] || "Appliances > Kitchen > Dishwashers";

  const mfrUrl = fields["MFR URL"] || (data.sources && data.sources[0]) || "";
  if (mfrUrl) {
    entityUrl.href = mfrUrl;
    entityUrlText.textContent = mfrUrl.replace("https://", "").replace("www.", "").substring(0, 35) + "...";
    entityUrl.style.display = "inline-flex";
  } else {
    entityUrl.style.display = "none";
  }

  // 3. Render Current Description Tab
  renderDescriptionTab(currentTab);

  // 4. Render 15 Attributes Cards
  renderAttributesGrid(fields, fieldSources, validation.needs_review_fields || []);

  // 5. Render Master 252 Table
  renderMasterTable(fields, fieldSources);
}


// ---------------------------------------------------------------------------
// Render Description Tabs
// ---------------------------------------------------------------------------

function renderDescriptionTab(tabKey) {
  currentTab = tabKey;
  const config = DESC_CONFIGS[tabKey];
  if (!config || !currentRecord) return;

  const fields = currentRecord.fields || {};
  const text = fields[config.field] || "(Empty / Needs Review)";
  const charLen = text.length;

  descGuideline.textContent = config.guideline;
  descTextOutput.textContent = text;

  // Character Limit Badge
  if (config.checkType === "max") {
    const isOk = charLen <= config.maxChars;
    charPill.textContent = `${charLen} / ${config.maxChars} chars`;
    charPill.className = `char-pill ${isOk ? "pill-success" : "pill-warning"}`;
  } else if (config.checkType === "range") {
    const isOk = charLen >= config.minChars && charLen <= config.maxChars;
    charPill.textContent = `${charLen} chars (${config.minChars}-${config.maxChars} target)`;
    charPill.className = `char-pill ${isOk ? "pill-success" : "pill-warning"}`;
  } else {
    charPill.textContent = `${charLen} chars`;
    charPill.className = "char-pill pill-success";
  }
}


// ---------------------------------------------------------------------------
// Render 15 Technical Attributes Grid
// ---------------------------------------------------------------------------

function renderAttributesGrid(fields, fieldSources, needsReviewList) {
  attributesGrid.innerHTML = "";

  for (let i = 1; i <= 15; i++) {
    const label = ATTRIBUTE_LABELS[i - 1];
    const valCol = `ATTRIBUTE_VALUE ${i}`;
    const uomCol = `ATTRIBUTE_UOM ${i}`;

    const val = fields[valCol] || "";
    const uom = fields[uomCol] || "";
    const source = fieldSources[valCol] || (val ? "research" : "empty");

    let tagClass = "research";
    let tagText = "Research";

    if (!val || needsReviewList.includes(valCol)) {
      tagClass = "needs-review";
      tagText = "Needs Review";
    } else if (source === "research_pdf") {
      tagClass = "research_pdf";
      tagText = "PDF Doc";
    } else if (source === "derived") {
      tagClass = "derived";
      tagText = "Derived";
    } else if (source === "constant") {
      tagClass = "derived";
      tagText = "Constant";
    }

    const card = document.createElement("div");
    card.className = "attr-card";
    card.innerHTML = `
      <div class="attr-card-header">
        <span class="attr-slot">Slot ${i}</span>
        <span class="attr-provenance-tag ${tagClass}">${tagText}</span>
      </div>
      <span class="attr-name">${label}</span>
      <div class="attr-value-row">
        <span class="attr-value ${val ? "" : "empty"}">${val || "—"}</span>
        ${uom ? `<span class="attr-uom">${uom}</span>` : ""}
      </div>
    `;
    attributesGrid.appendChild(card);
  }
}


// ---------------------------------------------------------------------------
// Render Master 252-Column Table
// ---------------------------------------------------------------------------

function renderMasterTable(fields, fieldSources) {
  masterTableBody.innerHTML = "";
  const entries = Object.entries(fields);

  entries.forEach(([colName, colVal], idx) => {
    const tr = document.createElement("tr");
    const source = fieldSources[colName] || (colVal ? "derived" : "empty");

    tr.innerHTML = `
      <td class="table-idx">${idx + 1}</td>
      <td class="table-col-header">${colName}</td>
      <td class="table-val">${colVal ? colVal : '<span style="color:var(--text-dim)">—</span>'}</td>
      <td><span class="attr-provenance-tag ${source}">${source}</span></td>
    `;
    masterTableBody.appendChild(tr);
  });
}


// ---------------------------------------------------------------------------
// Event Listeners
// ---------------------------------------------------------------------------

// Form Submit
enrichForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const mpn = mpnInput.value;
  const desc = descInput.value;
  const dist = distributorInput.value;
  const force = refreshCheckbox.checked;
  enrichProduct(mpn, desc, dist, force);
});

// Preset Buttons Click
presetButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    presetButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    const mpn = btn.dataset.mpn;
    const desc = btn.dataset.desc;

    mpnInput.value = mpn;
    descInput.value = desc;

    enrichProduct(mpn, desc, distributorInput.value, false);
  });
});

// Description Tabs Click
descTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    descTabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    renderDescriptionTab(tab.dataset.tab);
  });
});

// Download CSV Button
downloadCsvBtn.addEventListener("click", () => {
  window.open(`${API_BASE}/api/catalog/export`, "_blank");
});

// Download JSON Audit Button
downloadJsonBtn.addEventListener("click", () => {
  if (!currentRecord) {
    alert("Please enrich a product first.");
    return;
  }
  const auditData = {
    mpn: currentRecord.mpn,
    brand: currentRecord.brand,
    validation: currentRecord.validation,
    field_provenance: currentRecord.field_sources,
    timestamp: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(auditData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${currentRecord.mpn}_Audit_Report.json`;
  a.click();
  URL.revokeObjectURL(url);
});


// ---------------------------------------------------------------------------
// Initialize Default Load
// ---------------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  // Auto-enrich default preset (PDSH4816AF)
  enrichProduct("PDSH4816AF", "PDSH4816AF Dishwasher SS - Display Only", "Appliance Dealers Cooperative (APPDE)", false);
});


// ===========================================================================
// Batch CSV Upload Module
// ===========================================================================

(function initBatchUpload() {
  // DOM refs
  const batchDropZone = document.getElementById("batch-drop-zone");
  const batchFileInput = document.getElementById("batch-file-input");
  const selectedFileInfo = document.getElementById("selected-file-info");
  const selectedFileName = document.getElementById("selected-file-name");
  const selectedFileRows = document.getElementById("selected-file-rows");
  const clearFileBtn = document.getElementById("clear-file-btn");
  const batchEnrichBtn = document.getElementById("batch-enrich-btn");
  const batchBtnText = batchEnrichBtn.querySelector(".btn-text");
  const batchBtnLoader = batchEnrichBtn.querySelector(".btn-loader");
  const batchProgress = document.getElementById("batch-progress");
  const batchProgressLabel = document.getElementById("batch-progress-label");
  const batchProgressCount = document.getElementById("batch-progress-count");
  const batchProgressFill = document.getElementById("batch-progress-fill");
  const batchProgressMpn = document.getElementById("batch-progress-mpn");
  const batchResultsCard = document.getElementById("batch-results-card");
  const batchResultsBody = document.getElementById("batch-results-body");
  const batchResultsSummary = document.getElementById("batch-results-summary");

  let selectedFile = null;
  let parsedRowCount = 0;

  // --- File Selection ---
  function handleFileSelect(file) {
    if (!file || !file.name.toLowerCase().endsWith(".csv")) {
      alert("Please select a .csv file.");
      return;
    }
    selectedFile = file;

    // Count rows by reading the file
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target.result;
      const lines = text.trim().split("\n").filter((l) => l.trim());
      parsedRowCount = Math.max(0, lines.length - 1); // subtract header row

      selectedFileName.textContent = file.name;
      selectedFileRows.textContent = `${parsedRowCount} row${parsedRowCount !== 1 ? "s" : ""}`;
      selectedFileInfo.classList.remove("hidden");
      batchEnrichBtn.disabled = parsedRowCount === 0;
    };
    reader.readAsText(file);
  }

  batchFileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      handleFileSelect(e.target.files[0]);
    }
  });

  // Drag & Drop
  batchDropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    batchDropZone.classList.add("drag-over");
  });

  batchDropZone.addEventListener("dragleave", () => {
    batchDropZone.classList.remove("drag-over");
  });

  batchDropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    batchDropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length > 0) {
      handleFileSelect(e.dataTransfer.files[0]);
      // Also set the input so the form data reads it
      const dt = new DataTransfer();
      dt.items.add(e.dataTransfer.files[0]);
      batchFileInput.files = dt.files;
    }
  });

  // Clear file
  clearFileBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    selectedFile = null;
    parsedRowCount = 0;
    batchFileInput.value = "";
    selectedFileInfo.classList.add("hidden");
    batchEnrichBtn.disabled = true;
  });

  // --- Batch Enrichment via SSE ---
  batchEnrichBtn.addEventListener("click", async () => {
    if (!selectedFile) {
      alert("Please select a CSV file first.");
      return;
    }

    // UI: enter processing state
    batchBtnText.classList.add("hidden");
    batchBtnLoader.classList.remove("hidden");
    batchEnrichBtn.disabled = true;
    batchProgress.classList.remove("hidden");
    batchProgress.classList.add("active");
    batchProgressLabel.textContent = "Uploading CSV...";
    batchProgressCount.textContent = `0 / ${parsedRowCount}`;
    batchProgressFill.style.width = "0%";
    batchProgressMpn.textContent = "";
    batchResultsCard.classList.add("hidden");
    batchResultsBody.innerHTML = "";
    batchResultsSummary.innerHTML = "";

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);

      const response = await fetch(`${API_BASE}/api/enrich-batch`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Batch upload failed");
      }

      // Read SSE stream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completedRows = 0;
      const allResults = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE events from buffer
        const events = buffer.split("\n\n");
        buffer = events.pop() || ""; // keep incomplete event in buffer

        for (const event of events) {
          const dataLine = event.trim();
          if (!dataLine.startsWith("data: ")) continue;

          try {
            const eventData = JSON.parse(dataLine.substring(6));

            if (eventData.type === "progress") {
              const pct = Math.round((eventData.current / eventData.total) * 100);
              batchProgressLabel.textContent = `Processing row ${eventData.current} of ${eventData.total}...`;
              batchProgressMpn.textContent = `🔧 MPN: ${eventData.mpn}`;
              // Don't update fill here — update on complete
            }

            if (eventData.type === "pacing") {
              batchProgressLabel.textContent = `⏳ Rate-limit cooldown (${eventData.wait_seconds}s) before row ${eventData.current + 1}...`;
            }

            if (eventData.type === "row_complete") {
              completedRows++;
              const pct = Math.round((completedRows / eventData.total) * 100);
              batchProgressFill.style.width = `${pct}%`;
              batchProgressCount.textContent = `${completedRows} / ${eventData.total}`;

              allResults.push(eventData.result);
              appendBatchResultRow(completedRows, eventData.result);
              batchResultsCard.classList.remove("hidden");
            }

            if (eventData.type === "done") {
              batchProgressLabel.textContent = `✅ Batch complete! ${eventData.total_processed} items processed.`;
              batchProgressFill.style.width = "100%";
              batchProgress.classList.remove("active");
              renderBatchSummary(allResults);
            }
          } catch (parseErr) {
            // Skip malformed events
          }
        }
      }
    } catch (err) {
      alert(`Batch Error: ${err.message}`);
      batchProgress.classList.add("hidden");
    } finally {
      batchBtnText.classList.remove("hidden");
      batchBtnLoader.classList.add("hidden");
      batchEnrichBtn.disabled = false;
      batchProgress.classList.remove("active");
    }
  });

  // --- Render batch result rows incrementally ---
  function appendBatchResultRow(idx, result) {
    const tr = document.createElement("tr");
    const confPct = Math.round((result.confidence_score || 0) * 100);
    const confClass = result.confidence === "HIGH" ? "badge-high" : result.confidence === "MEDIUM" ? "badge-medium" : "badge-low";
    const validText = result.is_valid ? "✅ Yes" : "❌ No";
    const validClass = result.is_valid ? "yes" : "no";
    const statusClass = result.status === "success" ? "success" : "error";

    tr.innerHTML = `
      <td class="table-idx">${idx}</td>
      <td class="mpn-cell">${result.mpn}</td>
      <td class="brand-cell">${result.resolved_brand || "—"}</td>
      <td>${result.research_status || "—"} <span style="color:var(--text-dim)">(${result.sources_count || 0} src)</span></td>
      <td class="confidence-cell"><span class="conf-badge ${confClass}" style="font-size:10px">${result.confidence}</span> ${confPct}%</td>
      <td class="valid-cell ${validClass}">${validText}</td>
      <td>${result.needs_review_fields_count || 0}</td>
      <td><span class="status-tag ${statusClass}">${result.status === "success" ? "OK" : "ERR"}</span></td>
    `;
    batchResultsBody.appendChild(tr);
  }

  // --- Render summary pills ---
  function renderBatchSummary(results) {
    const validCount = results.filter((r) => r.is_valid).length;
    const highCount = results.filter((r) => r.confidence === "HIGH").length;
    const medCount = results.filter((r) => r.confidence === "MEDIUM").length;
    const lowCount = results.filter((r) => r.confidence === "LOW").length;

    batchResultsSummary.innerHTML = `
      <span class="batch-summary-pill valid"><i class="fa-solid fa-check-circle"></i> ${validCount}/${results.length} Valid</span>
      ${highCount > 0 ? `<span class="batch-summary-pill high">${highCount} HIGH</span>` : ""}
      ${medCount > 0 ? `<span class="batch-summary-pill medium">${medCount} MEDIUM</span>` : ""}
      ${lowCount > 0 ? `<span class="batch-summary-pill low">${lowCount} LOW</span>` : ""}
    `;
  }
})();
