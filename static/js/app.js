// ---------- Tab navigation ----------
const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
tabs.forEach(tab => {
  tab.addEventListener("click", () => {
    tabs.forEach(t => t.classList.remove("active"));
    panels.forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "insights") loadModelInfo();
    if (tab.dataset.tab === "history") renderHistory();
    if (tab.dataset.tab === "dashboard") loadDashboard();
  });
});

// ---------- Helpers ----------
const $ = id => document.getElementById(id);
function setLoading(btn, spinner, loading) {
  btn.disabled = loading;
  spinner.hidden = !loading;
}
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

// ---------- Feedback: "Was this prediction correct?" ----------
// Draws Yes / No buttons inside `container` for one saved scan and sends the
// answer to the server (POST /api/scans/<id>/feedback). `current` is the
// answer already saved for this scan: true, false, or null (not answered).
// Passing scanId = null hides the box (used when there is no saved scan).
function renderFeedback(container, scanId, current) {
  if (scanId == null) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const answer = current === true || current === false ? current : null;
  container.className = "feedback-box";
  container.hidden = false;
  container.innerHTML = `
    <span class="feedback-q">Was this prediction correct?</span>
    <button type="button" class="btn btn-secondary feedback-btn yes${answer === true ? " active" : ""}" data-val="true">Yes</button>
    <button type="button" class="btn btn-secondary feedback-btn no${answer === false ? " active" : ""}" data-val="false">No</button>
    <span class="feedback-status">${answer === null ? "" : "Saved — thanks!"}</span>
  `;
  const status = container.querySelector(".feedback-status");
  container.querySelectorAll(".feedback-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const isCorrect = btn.dataset.val === "true";
      status.textContent = "Saving…";
      try {
        const res = await fetch(`/api/scans/${scanId}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_correct: isCorrect }),
        });
        const out = await res.json();
        if (!res.ok || !out.success) throw new Error(out.error || "Could not save your answer.");
        container.querySelectorAll(".feedback-btn").forEach(b => b.classList.toggle("active", b === btn));
        status.textContent = "Saved — thanks!";
      } catch (err) {
        status.textContent = err.message;
      }
    });
  });
}

// Import provenance for whatever is currently in the form, so the detailed
// report / PDF can show "Source: <url>" when the posting was imported.
// Cleared on "Clear all" so a manually-entered posting doesn't carry a
// stale source over from a previous import.
let lastImportMeta = null;
let lastRenderedResult = null;
let lastVerification = null;
let lastRenderedPayload = null;

// ---------- URL import ----------
$("import-btn").addEventListener("click", async () => {
  const url = $("url-input").value.trim();
  const statusEl = $("import-status");
  const btn = $("import-btn");
  const spinner = btn.querySelector(".spinner");
  statusEl.textContent = "";
  statusEl.className = "import-status";

  if (!url) {
    statusEl.textContent = "Enter a URL first.";
    statusEl.classList.add("err");
    return;
  }

  setLoading(btn, spinner, true);
  try {
    const res = await fetch("/api/import-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || "Could not import that URL.");
    }
    fillForm(data.job);
    lastImportMeta = { source_url: data.job.source_url || url, extraction_method: data.job.extraction_method || "" };
    const derived = data.job.derived_fields || [];
    const derivedNote = derived.length
      ? ` Auto-derived from the text (double-check): ${derived.join(", ")}.`
      : "";
    statusEl.textContent = `Imported via ${data.job.extraction_method}.${derivedNote} Review before scanning.`;
    statusEl.classList.add("ok");
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.classList.add("err");
  } finally {
    setLoading(btn, spinner, false);
  }
});

function fillForm(job) {
  $("f-title").value = job.title || "";
  $("f-company").value = job.company_profile || "";
  $("f-description").value = job.description || "";
  $("f-requirements").value = job.requirements || "";
  $("f-benefits").value = job.benefits || "";
  $("f-salary").value = job.salary_range || "";
  $("f-industry").value = job.industry && job.industry !== "Unknown" ? job.industry : "";
  $("f-function").value = job.function && job.function !== "Unknown" ? job.function : "";

  setSelectIfPresent("f-employment", job.employment_type);
  setSelectIfPresent("f-experience", job.required_experience);
  setSelectIfPresent("f-education", job.required_education);

  $("f-logo").checked = !!job.has_company_logo;
  $("f-remote").checked = !!job.telecommuting;
  $("f-questions").checked = !!job.has_questions;
}
function setSelectIfPresent(id, value) {
  const el = $(id);
  if (!value) return;
  // exact match first
  for (const opt of el.options) {
    if (opt.value.toLowerCase() === value.toLowerCase()) { el.value = opt.value; return; }
  }
  // loose contains-match fallback (handles e.g. LinkedIn's "Mid-Senior level" vs our option text)
  for (const opt of el.options) {
    if (opt.value && (opt.value.toLowerCase().includes(value.toLowerCase()) || value.toLowerCase().includes(opt.value.toLowerCase()))) {
      el.value = opt.value;
      return;
    }
  }
}

// ---------- Clear form ----------
$("clear-btn").addEventListener("click", () => {
  ["f-title","f-company","f-description","f-requirements","f-benefits","f-salary","f-industry","f-function"]
    .forEach(id => $(id).value = "");
  ["f-employment","f-experience","f-education"].forEach(id => $(id).selectedIndex = 0);
  ["f-logo","f-remote","f-questions"].forEach(id => $(id).checked = false);
  $("url-input").value = "";
  $("paste-input").value = "";
  $("import-status").textContent = "";
  lastImportMeta = null;
  lastRenderedResult = null;
  lastRenderedPayload = null;
  $("detailed-report-card").hidden = true;
  renderFeedback($("feedback-inline"), null, null);
});

// ---------- Paste-text import ----------
$("parse-text-btn").addEventListener("click", async () => {
  const text = $("paste-input").value.trim();
  const statusEl = $("import-status");
  const btn = $("parse-text-btn");
  const spinner = btn.querySelector(".spinner");
  statusEl.textContent = "";
  statusEl.className = "import-status";

  if (!text) {
    statusEl.textContent = "Paste some job posting text first.";
    statusEl.classList.add("err");
    return;
  }

  setLoading(btn, spinner, true);
  try {
    const res = await fetch("/api/parse-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || "Could not parse that text.");
    }
    fillForm(data.job);
    lastImportMeta = { source_url: data.job.source_url || "", extraction_method: data.job.extraction_method || "pasted text" };
    const derived = data.job.derived_fields || [];
    const derivedNote = derived.length
      ? ` Auto-derived from the text (double-check): ${derived.join(", ")}.`
      : "";
    statusEl.textContent = `Parsed pasted text.${derivedNote} Review before scanning.`;
    statusEl.classList.add("ok");
  } catch (err) {
    statusEl.textContent = err.message;
    statusEl.classList.add("err");
  } finally {
    setLoading(btn, spinner, false);
  }
});

// ---------- Analyze ----------
$("analyze-btn").addEventListener("click", async () => {
  const btn = $("analyze-btn");
  const spinner = btn.querySelector(".spinner");
  const errorEl = $("analyze-error");
  errorEl.textContent = "";
  renderFeedback($("feedback-inline"), null, null);   // hide the previous scan's feedback buttons

  const payload = {
    title: $("f-title").value,
    company_profile: $("f-company").value,
    description: $("f-description").value,
    requirements: $("f-requirements").value,
    benefits: $("f-benefits").value,
    salary_range: $("f-salary").value,
    employment_type: $("f-employment").value,
    required_experience: $("f-experience").value,
    required_education: $("f-education").value,
    industry: $("f-industry").value,
    function: $("f-function").value,
    has_company_logo: $("f-logo").checked ? 1 : 0,
    telecommuting: $("f-remote").checked ? 1 : 0,
    has_questions: $("f-questions").checked ? 1 : 0,
  };

  setLoading(btn, spinner, true);
  try {
    const res = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    lastRenderedResult = data;
    lastRenderedPayload = payload;
    lastVerification = null;
    renderResult(data, payload);
    renderDetailedReportInline(payload, data, lastImportMeta, null);
    const historyId = await saveToHistory(data, payload, lastImportMeta);
    renderFeedback($("feedback-inline"), historyId, null);   // show Yes / No buttons for this saved scan
    runVerification(payload, lastImportMeta, historyId);
  } catch (err) {
    errorEl.textContent = err.message;
  } finally {
    setLoading(btn, spinner, false);
  }
});

// ---------- External verification (async, runs after the ML verdict) ----------
async function runVerification(payload, meta, historyId) {
  const wrap = $("verify-wrap");
  const body = $("verify-body");
  wrap.hidden = false;
  body.innerHTML = `<p class="verify-loading">Checking company domain(s) &amp; email(s) against live DNS / WHOIS / HTTP…</p>`;
  try {
    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record: payload, source_url: meta && meta.source_url }),
    });
    const data = await res.json();
    if (!res.ok || data.success === false) throw new Error(data.error || "Verification failed.");
    body.innerHTML = buildVerificationHTML(data);
    if (historyId) updateHistoryVerification(historyId, data);
    if (lastRenderedPayload === payload) {
      lastVerification = data;
      renderDetailedReportInline(payload, lastRenderedResult, meta, data);
    }
  } catch (err) {
    body.innerHTML = `<p class="verify-loading">Couldn't complete external verification: ${escapeHtml(err.message)}</p>`;
  }
}

function buildVerificationHTML(v) {
  if (v.nothing_to_check) {
    return `<p class="verify-empty">No company website or recruiter email address was found in the posting text to check.</p>`;
  }
  const flagsHtml = v.verification_flags && v.verification_flags.length
    ? `<ul class="verify-flags">${v.verification_flags.map(f => `<li>⚠ ${escapeHtml(f)}</li>`).join("")}</ul>`
    : `<p class="verify-ok">✓ No issues found across ${v.domains_found.length} domain(s) and ${v.emails_found.length} email(s) checked.</p>`;

  const domainRows = (v.domain_checks || []).map(d => {
    const age = d.whois && d.whois.age_days != null ? `${d.whois.age_days} days old (registered ${d.whois.created})` : (d.whois && d.whois.error ? d.whois.error : "age unknown");
    const reach = d.reachable === true ? "reachable" : d.reachable === false ? "unreachable" : "unknown";
    return `<div class="verify-row"><span class="verify-domain">${escapeHtml(d.domain)}</span><span class="verify-meta">${reach} · ${escapeHtml(age)}</span></div>`;
  }).join("");

  const emailRows = (v.email_checks || []).map(e => {
    const mx = e.has_mx === true ? "has mail server" : e.has_mx === false ? "no mail server" : "MX unknown";
    return `<div class="verify-row"><span class="verify-domain">${escapeHtml(e.email)}</span><span class="verify-meta">${e.is_free_provider ? "free provider" : "company domain"} · ${mx}</span></div>`;
  }).join("");

  return `
    ${flagsHtml}
    ${domainRows ? `<div class="verify-section-title">Domains checked</div>${domainRows}` : ""}
    ${emailRows ? `<div class="verify-section-title">Emails checked</div>${emailRows}` : ""}
    <p class="verify-timestamp">Checked ${escapeHtml(v.checked_at)}</p>
  `;
}

function renderResult(data, payload) {
  $("result-empty").hidden = true;
  const card = $("result-card");
  card.hidden = false;

  const isFraud = data.prediction === 1;
  const badge = $("verdict-badge");
  badge.className = "verdict-badge " + (isFraud ? "fraud" : "legit");
  $("verdict-label").textContent = isFraud ? "⚠ Likely Fraudulent" : "✓ Likely Legitimate";

  // Gauge
  const circumference = 2 * Math.PI * 52;
  const fillEl = $("gauge-fill");
  fillEl.style.stroke = isFraud ? "var(--fraud)" : "var(--legit)";
  const offset = circumference * (1 - data.confidence / 100);
  fillEl.style.strokeDasharray = circumference;
  requestAnimationFrame(() => { fillEl.style.strokeDashoffset = offset; });
  $("gauge-value").textContent = `${data.confidence}%`;

  // Probability bars
  requestAnimationFrame(() => {
    $("legit-bar").style.width = data.legit_probability + "%";
    $("fraud-bar").style.width = data.fraud_probability + "%";
  });
  $("legit-pct").textContent = data.legit_probability + "%";
  $("fraud-pct").textContent = data.fraud_probability + "%";

  // Risk factors / positive factors
  const riskWrap = $("risk-factors-wrap");
  const riskList = $("risk-list");
  const positiveWrap = $("positive-factors-wrap");
  const positiveList = $("positive-list");
  riskList.innerHTML = "";
  positiveList.innerHTML = "";
  if (data.risk_factors && data.risk_factors.length) {
    data.risk_factors.forEach(r => {
      const li = document.createElement("li");
      li.textContent = r;
      riskList.appendChild(li);
    });
    riskWrap.hidden = false;
    positiveWrap.hidden = true;
  } else {
    riskWrap.hidden = true;
    if (data.positive_factors && data.positive_factors.length) {
      data.positive_factors.forEach(r => {
        const li = document.createElement("li");
        li.textContent = r;
        positiveList.appendChild(li);
      });
      positiveWrap.hidden = false;
    } else {
      positiveWrap.hidden = true;
    }
  }

  // Structured-field impact (counterfactual ablation)
  renderStructuredImpact($("structured-impact-wrap"), $("structured-impact-list"), data.structured_impact);

  // Explainability chips (SHAP word scores when available, plain words as fallback)
  renderChips("fraud-words", data.fraud_word_scores, data.fraud_words, "fraud");
  renderChips("legit-words", data.legit_word_scores, data.legit_words, "legit");
  $("explain-method-note").textContent = explainMethodNote(data);

  $("model-used-label").textContent = data.model_used;

  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function explainMethodNote(data) {
  if (data.explanation_method === "shap-partition-text") {
    const base = data.explanation_base_value != null
      ? ` · baseline fraud probability across the training background: ${(data.explanation_base_value * 100).toFixed(1)}%`
      : "";
    const trunc = data.explanation_truncated ? " · posting truncated to first 300 tokens for performance" : "";
    return `Word attributions computed with SHAP (Partition / text-masking explainer) — model-agnostic, exact Shapley values, not a linear-model approximation${base}${trunc}.`;
  }
  if (data.explanation_method === "lr-coefficient-fallback") {
    return "SHAP was unavailable, so word attributions fall back to a Logistic-Regression-coefficient approximation (install the `shap` package for exact, model-agnostic Shapley values).";
  }
  return "";
}

function renderChips(containerId, scores, plainWords, kind) {
  const el = $(containerId);
  el.innerHTML = "";
  const hasScores = scores && scores.length;
  const words = hasScores ? null : plainWords;
  if (!hasScores && (!words || !words.length)) {
    el.innerHTML = `<span class="chip-empty">No strong signal words found</span>`;
    return;
  }
  if (hasScores) {
    const maxAbs = Math.max(...scores.map(s => Math.abs(s.impact)), 0.0001);
    scores.forEach(s => {
      const span = document.createElement("span");
      const intensity = Math.min(1, Math.abs(s.impact) / maxAbs);
      span.className = `chip ${kind}`;
      span.style.opacity = (0.55 + intensity * 0.45).toFixed(2);
      span.title = `Shapley impact: ${s.impact > 0 ? "+" : ""}${s.impact.toFixed(3)}`;
      span.textContent = s.word;
      el.appendChild(span);
    });
    return;
  }
  words.forEach(w => {
    const span = document.createElement("span");
    span.className = `chip ${kind}`;
    span.textContent = w;
    el.appendChild(span);
  });
}

function renderStructuredImpact(wrapEl, listEl, impacts) {
  listEl.innerHTML = "";
  if (!impacts || !impacts.length) {
    wrapEl.hidden = true;
    return;
  }
  wrapEl.hidden = false;
  const maxAbs = Math.max(...impacts.map(i => Math.abs(i.delta_pp)), 0.01);
  impacts.forEach(i => {
    const row = document.createElement("div");
    row.className = "impact-row";
    const pct = Math.min(100, (Math.abs(i.delta_pp) / maxAbs) * 100);
    const sign = i.delta_pp > 0 ? "+" : "";
    row.innerHTML = `
      <div class="impact-label">
        <span class="impact-field">${escapeHtml(i.label)}</span>
        <span class="impact-value">${escapeHtml(String(i.value))}</span>
      </div>
      <div class="impact-track">
        <div class="impact-fill ${i.direction}" style="width:${pct}%"></div>
      </div>
      <span class="impact-delta ${i.direction}">${sign}${i.delta_pp.toFixed(1)} pp</span>
    `;
    listEl.appendChild(row);
  });
}

// ---------- Detailed report (shared by Analyze inline card + History modal) ----------
const FIELD_LABELS = [
  ["title", "Job title"],
  ["employment_type", "Employment type"],
  ["required_experience", "Required experience"],
  ["required_education", "Required education"],
  ["industry", "Industry"],
  ["function", "Job function"],
  ["salary_range", "Salary range"],
];
const BOOL_FIELD_LABELS = [
  ["has_company_logo", "Company logo shown"],
  ["telecommuting", "Telecommuting / remote"],
  ["has_questions", "Screening questions present"],
];
const LONG_TEXT_FIELDS = [
  ["company_profile", "Company Profile"],
  ["description", "Job Description"],
  ["requirements", "Requirements"],
  ["benefits", "Benefits"],
];

function fieldValue(record, key) {
  const v = record[key];
  if (v === undefined || v === null || v === "" || v === "Unknown") return "Not specified";
  return v;
}

// Wraps the model's influential words inside a block of text in <mark> tags
// (red = pushed toward fraudulent, green = pushed toward legitimate).
function highlightWords(text, fraudWords, legitWords) {
  const fraud = new Set((fraudWords || []).map(w => String(w).toLowerCase()));
  const legit = new Set((legitWords || []).map(w => String(w).toLowerCase()));
  const all = [...new Set([...fraud, ...legit])]
    .filter(w => w.length > 1)
    .sort((a, b) => b.length - a.length);
  if (!text || !all.length) return escapeHtml(text || "");
  const re = new RegExp(
    "\\b(" + all.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\b", "gi"
  );
  let out = "", last = 0, m;
  while ((m = re.exec(text)) !== null) {
    out += escapeHtml(text.slice(last, m.index));
    const cls = fraud.has(m[0].toLowerCase()) ? "hl-fraud" : "hl-legit";
    out += `<mark class="${cls}">${escapeHtml(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + escapeHtml(text.slice(last));
}

function buildReportHTML(record, result, meta, scannedAt, verification) {
  const isFraud = result.prediction === 1;
  const verdictLabel = isFraud ? "⚠ Likely Fraudulent" : "✓ Likely Legitimate";
  const companyName = (record.company_profile || "").split("—")[0].trim();

  // Only show fields that actually have a value — "Not specified" rows are noise.
  const fieldRows = FIELD_LABELS.filter(([key]) => fieldValue(record, key) !== "Not specified").map(([key, label]) => `
    <div class="report-field"><span class="k">${escapeHtml(label)}</span><span class="v">${escapeHtml(fieldValue(record, key))}</span></div>
  `).join("");
  const boolRows = BOOL_FIELD_LABELS.map(([key, label]) => `
    <div class="report-field"><span class="k">${escapeHtml(label)}</span><span class="v">${record[key] ? "Yes" : "No"}</span></div>
  `).join("");

  const riskHtml = (result.risk_factors && result.risk_factors.length)
    ? `<div><h4 class="report-subhead risk-color">Risk signals</h4><ul class="risk-list">${result.risk_factors.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ul></div>`
    : (result.positive_factors && result.positive_factors.length)
      ? `<div><h4 class="report-subhead legit-color">Why this looks legitimate</h4><ul class="risk-list positive-list">${result.positive_factors.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ul></div>`
      : `<p class="report-section-empty">No heuristic risk signals were triggered for this posting.</p>`;

  const chips = (scores, words, kind) => {
    if (scores && scores.length) {
      const maxAbs = Math.max(...scores.map(s => Math.abs(s.impact)), 0.0001);
      return `<div class="chip-row">${scores.map(s => {
        const intensity = (0.55 + Math.min(1, Math.abs(s.impact) / maxAbs) * 0.45).toFixed(2);
        return `<span class="chip ${kind}" style="opacity:${intensity}" title="Shapley impact: ${s.impact > 0 ? "+" : ""}${s.impact.toFixed(3)}">${escapeHtml(s.word)}</span>`;
      }).join("")}</div>`;
    }
    if (words && words.length) {
      return `<div class="chip-row">${words.map(w => `<span class="chip ${kind}">${escapeHtml(w)}</span>`).join("")}</div>`;
    }
    return `<span class="chip-empty">No strong signal words found</span>`;
  };

  const impactHtml = (result.structured_impact && result.structured_impact.length)
    ? `<div class="impact-list">${result.structured_impact.map(i => {
        const maxAbs = Math.max(...result.structured_impact.map(x => Math.abs(x.delta_pp)), 0.01);
        const pct = Math.min(100, (Math.abs(i.delta_pp) / maxAbs) * 100);
        const sign = i.delta_pp > 0 ? "+" : "";
        return `<div class="impact-row">
          <div class="impact-label"><span class="impact-field">${escapeHtml(i.label)}</span><span class="impact-value">${escapeHtml(String(i.value))}</span></div>
          <div class="impact-track"><div class="impact-fill ${i.direction}" style="width:${pct}%"></div></div>
          <span class="impact-delta ${i.direction}">${sign}${i.delta_pp.toFixed(1)} pp</span>
        </div>`;
      }).join("")}</div>`
    : `<p class="report-section-empty">No individual structured field moved the prediction by more than the reporting threshold.</p>`;

  const methodLine = result.explanation_method === "shap-partition-text"
    ? `Word attributions: SHAP Partition/text explainer (model-agnostic Shapley values)${result.explanation_base_value != null ? ` · background baseline ${(result.explanation_base_value * 100).toFixed(1)}%` : ""}.`
    : result.explanation_method === "lr-coefficient-fallback"
      ? "Word attributions: Logistic-Regression-coefficient fallback (SHAP unavailable in this environment)."
      : "";

  const longTextHtml = LONG_TEXT_FIELDS.map(([key, label]) => {
    const val = (record[key] || "").trim();
    if (!val) return "";
    return `<details class="report-longtext" open><summary>${escapeHtml(label)}</summary><p>${highlightWords(val, result.fraud_words, result.legit_words)}</p></details>`;
  }).join("");

  const sourceLine = meta && meta.source_url
    ? `Source: ${escapeHtml(meta.source_url.split("?")[0])} (${escapeHtml(meta.extraction_method || "imported")})`
    : "Entered manually";

  const verifyHtml = (() => {
    if (!verification) return `<p class="report-section-empty">Not run for this scan.</p>`;
    if (verification.nothing_to_check) return `<p class="report-section-empty">No company website or recruiter email address was found in the posting text to check.</p>`;
    return buildVerificationHTML(verification);
  })();

  return `
    <div class="report-head">
      <div>
        <h3 class="report-job-title">${escapeHtml(record.title || "(untitled posting)")}</h3>
        ${companyName ? `<p class="report-company">${escapeHtml(companyName)}</p>` : ""}
      </div>
      <span class="report-verdict-chip ${isFraud ? "fraud" : "legit"}">${verdictLabel} · ${result.confidence}%</span>
    </div>

    <div class="report-section">
      <h4>Probability breakdown</h4>
      <div class="prob-bars">
        <div class="prob-row">
          <span class="prob-label">Legitimate</span>
          <div class="prob-track"><div class="prob-fill legit" style="width:${result.legit_probability}%"></div></div>
          <span class="prob-pct">${result.legit_probability}%</span>
        </div>
        <div class="prob-row">
          <span class="prob-label">Fraudulent</span>
          <div class="prob-track"><div class="prob-fill fraud" style="width:${result.fraud_probability}%"></div></div>
          <span class="prob-pct">${result.fraud_probability}%</span>
        </div>
      </div>
    </div>

    <div class="report-section">
      <h4>External verification <span class="method-note">(live domain / email checks)</span></h4>
      ${verifyHtml}
    </div>

    <div class="report-section">
      <h4>Structured-field impact <span class="method-note">(counterfactual ablation)</span></h4>
      ${impactHtml}
    </div>

    <div class="report-section">
      <h4>Heuristic risk signals</h4>
      ${riskHtml}
    </div>

    <div class="report-section">
      <h4>Model explainability — influential terms</h4>
      <div class="explain-grid">
        <div><h3 class="section-label fraud-color">Pushed toward FRAUDULENT</h3>${chips(result.fraud_word_scores, result.fraud_words, "fraud")}</div>
        <div><h3 class="section-label legit-color">Pushed toward LEGITIMATE</h3>${chips(result.legit_word_scores, result.legit_words, "legit")}</div>
      </div>
      ${methodLine ? `<p class="method-note">${escapeHtml(methodLine)}</p>` : ""}
    </div>

    <div class="report-section">
      <h4>Submitted job posting details</h4>
      <div class="report-grid">${fieldRows}${boolRows}</div>
    </div>

    ${longTextHtml ? `<div class="report-section">${longTextHtml}</div>` : ""}

    <p class="report-meta-line">Scanned: ${escapeHtml(scannedAt || new Date().toLocaleString())} · Model: ${escapeHtml(result.model_used)} · ${sourceLine}</p>
  `;
}

function renderDetailedReportInline(record, result, meta, verification) {
  const card = $("detailed-report-card");
  $("detailed-report-body").innerHTML = buildReportHTML(record, result, meta, new Date().toLocaleString(), verification);
  card.hidden = false;
}

// ---------- Report modal (History) ----------
const modalBackdrop = $("report-modal");
let activeModalEntry = null;

function openReportModal(entry) {
  activeModalEntry = entry;
  $("modal-report-body").innerHTML = buildReportHTML(entry.record, entry.result, entry.meta, entry.time, entry.verification);
  renderFeedback($("feedback-modal"), entry.id, entry.feedback);   // Yes / No buttons (shows the saved answer if any)
  modalBackdrop.hidden = false;
}
function closeReportModal() {
  modalBackdrop.hidden = true;
  activeModalEntry = null;
}
$("modal-close-btn").addEventListener("click", closeReportModal);
modalBackdrop.addEventListener("click", e => { if (e.target === modalBackdrop) closeReportModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !modalBackdrop.hidden) closeReportModal(); });

// ---------- PDF download ----------
async function downloadReportPdf(record, result, meta, btn, reportId, verification) {
  const spinner = btn.querySelector(".spinner");
  setLoading(btn, spinner, true);
  try {
    const res = await fetch("/api/report/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record, result, meta: Object.assign({ report_id: reportId }, meta || {}), verification: verification || null }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || "Could not generate the PDF.");
    }
    const blob = await res.blob();
    const slug = (record.title || "job-posting").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "job-posting";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `scamurai-report-${slug}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    alert(err.message);
  } finally {
    setLoading(btn, spinner, false);
  }
}

$("download-pdf-btn").addEventListener("click", () => {
  if (!lastRenderedResult) return;
  downloadReportPdf(lastRenderedPayload, lastRenderedResult, lastImportMeta, $("download-pdf-btn"), undefined, lastVerification);
});
$("modal-download-pdf-btn").addEventListener("click", () => {
  if (!activeModalEntry) return;
  downloadReportPdf(activeModalEntry.record, activeModalEntry.result, activeModalEntry.meta, $("modal-download-pdf-btn"), activeModalEntry.id, activeModalEntry.verification);
});

// ---------- Model insights ----------
let modelInfoLoaded = false;
async function loadModelInfo() {
  if (modelInfoLoaded) return;
  try {
    const res = await fetch("/api/model-info");
    const info = await res.json();
    const best = info.model_comparison[info.best_model];
    const tiles = [
      ["Best model", info.best_model],
      ["Accuracy", (best.accuracy * 100).toFixed(1) + "%"],
      ["Precision", (best.precision * 100).toFixed(1) + "%"],
      ["Recall", (best.recall * 100).toFixed(1) + "%"],
      ["F1 Score", best.f1.toFixed(3)],
      ["ROC-AUC", best.roc_auc.toFixed(3)],
      ["Training rows", info.dataset_rows.toLocaleString()],
      ["Fraud cases", info.fraudulent_count.toLocaleString()],
    ];
    const grid = $("metrics-cards");
    grid.innerHTML = tiles.map(([lbl, val]) => `
      <div class="metric-tile"><div class="val">${val}</div><div class="lbl">${lbl}</div></div>
    `).join("");

    renderCvSummary(info);
    renderFeedbackSummary(info);
    modelInfoLoaded = true;
  } catch (e) {
    $("metrics-cards").innerHTML = `<p class="error-text">Could not load model info.</p>`;
  }
}


// Cross-validated results (written by src/evaluate_cv.py --apply). The single
// 80/20 split above is optimistic with only ~5% fraud rows, so this block shows
// the 5-fold numbers and the tuned decision threshold the live app really uses.
function renderCvSummary(info) {
  if (!info.cv || !info.cv.default_threshold_metrics) return;
  let el = $("cv-summary");
  if (!el) {
    el = document.createElement("div");
    el.id = "cv-summary";
    el.className = "card";
    el.style.marginTop = "20px";
    $("metrics-cards").insertAdjacentElement("afterend", el);
  }
  const d = info.cv.default_threshold_metrics, t = info.cv.metrics_at_threshold || {};
  const pct = v => (v * 100).toFixed(1) + "%";
  el.innerHTML = `
    <h3 class="card-title">Cross-validated performance (${escapeHtml(info.cv.folds)}-fold)</h3>
    <p class="page-sub">More trustworthy than the single split above. Fraud class only.</p>
    <div class="metrics-grid" style="margin-top: 10px;">
      <div class="metric-tile"><div class="val">${pct(d.precision)}</div><div class="lbl">Precision @ 0.50</div></div>
      <div class="metric-tile"><div class="val">${pct(d.recall)}</div><div class="lbl">Recall @ 0.50</div></div>
      <div class="metric-tile"><div class="val">${d.f1.toFixed(3)}</div><div class="lbl">F1 @ 0.50</div></div>
      <div class="metric-tile"><div class="val">${d.pr_auc.toFixed(3)}</div><div class="lbl">PR-AUC</div></div>
    </div>
    ${info.decision_threshold ? `<p class="page-sub" style="margin-top: 12px;">Live decision threshold: <strong>${info.decision_threshold}</strong>
      &rarr; precision ${pct(t.precision)}, recall ${pct(t.recall)}, F1 ${(t.f1).toFixed(3)} (out-of-fold).</p>` : ""}
  `;
}

// Feedback collected so far via "Was this prediction correct?" — shown as
// its own small block under the main metrics tiles. feedback_rows_used only
// appears in info once src/retrain_from_feedback.py --apply has run at
// least once, so that line is left out until then.
function renderFeedbackSummary(info) {
  let el = $("feedback-summary");
  if (!el) {
    el = document.createElement("div");
    el.id = "feedback-summary";
    el.className = "card";
    el.style.marginTop = "20px";
    $("metrics-cards").insertAdjacentElement("afterend", el);
  }

  const total = info.feedback_total || 0;
  if (!total) {
    el.innerHTML = `
      <h3 class="card-title">Feedback collected</h3>
      <p class="page-sub">No feedback yet — answer "Was this prediction correct?" on a scan to start building retraining data.</p>
    `;
    return;
  }

  const usedLine = info.feedback_rows_used
    ? `<p class="page-sub">Last retrain used ${info.feedback_rows_used} feedback row(s).</p>`
    : `<p class="page-sub">Not yet used in a retrain — run <code>python src/retrain_from_feedback.py</code> to preview, then <code>--apply</code> to use it.</p>`;

  el.innerHTML = `
    <h3 class="card-title">Feedback collected</h3>
    <div class="metrics-grid" style="margin-top: 10px;">
      <div class="metric-tile"><div class="val">${total}</div><div class="lbl">Total answered</div></div>
      <div class="metric-tile"><div class="val legit-color">${info.feedback_correct || 0}</div><div class="lbl">Marked correct</div></div>
      <div class="metric-tile"><div class="val fraud-color">${info.feedback_incorrect || 0}</div><div class="lbl">Marked incorrect</div></div>
    </div>
    ${usedLine}
  `;
}

// ---------- History (persistent, server-side — per logged-in user) ----------
// Every scan is saved to the backend right after /api/predict returns, so
// history survives across browsers/devices and clearing the browser cache,
// unlike the old localStorage-only version. Each row belongs to exactly one
// account (enforced server-side), so this is safe for multiple users of the
// same deployment.
async function saveToHistory(data, payload, meta) {
  try {
    const res = await fetch("/api/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record: payload, result: data, meta: meta || null }),
    });
    const out = await res.json();
    if (!res.ok || !out.success) throw new Error(out.error || "Could not save to history.");
    return out.id;
  } catch (err) {
    console.error("saveToHistory failed:", err);
    return null;
  }
}
async function updateHistoryVerification(id, verification) {
  if (id == null) return;
  try {
    await fetch(`/api/scans/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verification }),
    });
  } catch (err) {
    console.error("updateHistoryVerification failed:", err);
  }
}
async function deleteHistoryEntry(id) {
  try {
    await fetch(`/api/scans/${id}`, { method: "DELETE" });
  } catch (err) {
    console.error("deleteHistoryEntry failed:", err);
  }
  renderHistory();
}

let historyScans = [];

async function renderHistory() {
  const empty = $("history-empty");
  const table = $("history-table");
  const tools = $("history-tools");
  const hint = $("history-hint");

  try {
    const res = await fetch("/api/scans");
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "Could not load history.");
    historyScans = out.scans || [];
  } catch (err) {
    empty.hidden = false;
    table.hidden = true;
    tools.hidden = true;
    hint.hidden = true;
    empty.textContent = `Couldn't load history: ${err.message}`;
    return;
  }
  drawHistory();
}

// Draws the table from the scans already loaded, applying search + filter.
function drawHistory() {
  const empty = $("history-empty");
  const table = $("history-table");
  const tools = $("history-tools");
  const body = $("history-body");
  const hint = $("history-hint");

  if (!historyScans.length) {
    empty.hidden = false;
    empty.innerHTML = "<p>No scans yet. Results you run in the Analyze tab will appear here.</p>";
    table.hidden = true;
    tools.hidden = true;
    hint.hidden = true;
    return;
  }

  tools.hidden = false;
  const q = $("history-search").value.trim().toLowerCase();
  const f = $("history-filter").value;
  const shown = historyScans.filter(h =>
    (f === "ALL" || h.label === f) &&
    (!q || (h.title || "").toLowerCase().includes(q))
  );
  $("history-count").textContent = `Showing ${shown.length} of ${historyScans.length}`;

  if (!shown.length) {
    empty.hidden = false;
    empty.innerHTML = "<p>No scans match your search or filter.</p>";
    table.hidden = true;
    hint.hidden = true;
    return;
  }
  empty.hidden = true;
  table.hidden = false;
  hint.hidden = false;

  body.innerHTML = shown.map(h => `
    <tr data-id="${h.id}">
      <td>${escapeHtml(h.time)}</td>
      <td>${escapeHtml(h.title)}</td>
      <td class="hist-verdict ${h.label === 'FRAUDULENT' ? 'fraud' : 'legit'}">${escapeHtml(h.label)}</td>
      <td>${h.confidence}%</td>
      <td><button class="hist-delete" data-id="${h.id}" title="Delete this entry">Delete</button></td>
    </tr>
  `).join("");

  body.querySelectorAll("tr[data-id]").forEach(row => {
    row.addEventListener("click", async e => {
      if (e.target.closest(".hist-delete")) return;
      try {
        const res = await fetch(`/api/scans/${row.dataset.id}`);
        const entry = await res.json();
        if (!res.ok) throw new Error(entry.error || "Could not load this scan.");
        openReportModal(entry);
      } catch (err) {
        alert(err.message);
      }
    });
  });
  body.querySelectorAll(".hist-delete").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      deleteHistoryEntry(btn.dataset.id);
    });
  });
}
$("history-search").addEventListener("input", drawHistory);
$("history-filter").addEventListener("change", drawHistory);

$("clear-history-btn").addEventListener("click", async () => {
  if (!confirm("Clear all scan history? This cannot be undone.")) return;
  try {
    await fetch("/api/scans", { method: "DELETE" });
  } catch (err) {
    alert("Could not clear history: " + err.message);
  }
  renderHistory();
});

// ---------- Dashboard ----------
// Every chart here is hand-rolled inline SVG rather than a charting
// library — this app already leans on Google Fonts over CDN, but the
// dashboard is meant to keep working even offline/air-gapped during a
// viva demo, and hand-rolled charts also mean every visual matches the
// app's own theme colors exactly instead of fighting a library's config.
async function loadDashboard() {
  let stats;
  try {
    const res = await fetch("/api/dashboard-stats");
    stats = await res.json();
    if (!res.ok) throw new Error(stats.error || "Could not load dashboard stats.");
  } catch (err) {
    $("dashboard-empty").hidden = false;
    $("dashboard-empty").querySelector("p").textContent = err.message;
    $("dashboard-content").hidden = true;
    return;
  }

  if (!stats.total_scans) {
    $("dashboard-empty").hidden = false;
    $("dashboard-content").hidden = true;
    return;
  }
  $("dashboard-empty").hidden = true;
  $("dashboard-content").hidden = false;

  $("dashboard-metrics").innerHTML = [
    ["val", stats.total_scans, "Total scans"],
    ["val fraud-color", `${stats.fraud_rate}%`, "Flagged fraudulent"],
    ["val", `${stats.avg_confidence}%`, "Avg. model confidence"],
    ["val", stats.verification_flagged ? `${stats.verification_flagged}/${stats.verification_checked}` : "0", "Verification checks flagged"],
  ].map(([cls, val, lbl]) => `
    <div class="metric-tile"><div class="${cls}">${val}</div><div class="lbl">${lbl}</div></div>
  `).join("");

  renderTrendChart($("trend-chart"), stats.trend);
  renderDonutChart($("donut-chart"), stats.legit_count, stats.fraud_count);
  renderBarChart($("risk-chart"), stats.top_risk_factors, "label", "count", "brand-fill", "No risk signals triggered yet.");
  renderBarChart($("words-chart"), stats.top_fraud_words, "word", "count", "fraud-fill", "No fraud-indicating terms captured yet.");
  renderEmploymentChart($("employment-chart"), stats.employment_breakdown);
}

function svgWrap(viewW, viewH, inner) {
  return `<svg viewBox="0 0 ${viewW} ${viewH}" preserveAspectRatio="none" class="svg-chart">${inner}</svg>`;
}

function renderTrendChart(el, trend) {
  if (!trend || !trend.length) { el.innerHTML = `<p class="chart-empty">No data yet.</p>`; return; }
  const W = 560, H = 220, padL = 30, padB = 26, padT = 14, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxVal = Math.max(1, ...trend.map(d => d.total));
  const stepX = trend.length > 1 ? plotW / (trend.length - 1) : 0;
  const xAt = i => padL + i * stepX;
  const yAt = v => padT + plotH - (v / maxVal) * plotH;

  const totalPath = trend.map((d, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(d.total).toFixed(1)}`).join(" ");
  const fraudPath = trend.map((d, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(d.fraud).toFixed(1)}`).join(" ");
  const areaPath = `${totalPath} L${xAt(trend.length - 1).toFixed(1)},${(padT + plotH).toFixed(1)} L${xAt(0).toFixed(1)},${(padT + plotH).toFixed(1)} Z`;

  const gridLines = [0, 0.5, 1].map(f => {
    const y = padT + plotH * f;
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="grid-line"/>`;
  }).join("");

  const xLabels = trend.map((d, i) => {
    if (trend.length > 8 && i % 2 !== 0 && i !== trend.length - 1) return "";
    return `<text x="${xAt(i)}" y="${H - 6}" class="chart-axis-label" text-anchor="middle">${d.date}</text>`;
  }).join("");

  const dots = trend.map((d, i) => d.fraud > 0
    ? `<circle cx="${xAt(i)}" cy="${yAt(d.fraud)}" r="3" class="fraud-fill"/>` : "").join("");

  el.innerHTML = svgWrap(W, H, `
    ${gridLines}
    <path d="${areaPath}" class="trend-area"/>
    <path d="${totalPath}" class="trend-line-total" fill="none"/>
    <path d="${fraudPath}" class="trend-line-fraud" fill="none"/>
    ${dots}
    ${xLabels}
  `) + `<div class="chart-legend">
      <span><i class="dot dim"></i>Total scans</span>
      <span><i class="dot fraud"></i>Flagged fraudulent</span>
    </div>`;
}

function renderDonutChart(el, legit, fraud) {
  const total = legit + fraud;
  const R = 60, CX = 80, CY = 80, STROKE = 22;
  const circumference = 2 * Math.PI * R;
  const fraudFrac = total ? fraud / total : 0;
  const fraudLen = circumference * fraudFrac;
  const legitLen = circumference - fraudLen;

  el.innerHTML = `
    <svg viewBox="0 0 160 160" class="donut-svg">
      <circle cx="${CX}" cy="${CY}" r="${R}" fill="none" class="donut-legit"
        stroke-width="${STROKE}" stroke-dasharray="${legitLen} ${circumference}" stroke-dashoffset="0"/>
      <circle cx="${CX}" cy="${CY}" r="${R}" fill="none" class="donut-fraud"
        stroke-width="${STROKE}" stroke-dasharray="${fraudLen} ${circumference}" stroke-dashoffset="${-legitLen}"/>
      <text x="${CX}" y="${CY - 4}" text-anchor="middle" class="donut-pct">${total ? Math.round(fraudFrac * 100) : 0}%</text>
      <text x="${CX}" y="${CY + 16}" text-anchor="middle" class="donut-pct-label">fraudulent</text>
    </svg>
    <div class="chart-legend">
      <span><i class="dot legit"></i>Legitimate (${legit})</span>
      <span><i class="dot fraud"></i>Fraudulent (${fraud})</span>
    </div>`;
}

function renderBarChart(el, items, labelKey, valueKey, colorClass, emptyText) {
  if (!items || !items.length) { el.innerHTML = `<p class="chart-empty">${emptyText}</p>`; return; }
  const max = Math.max(...items.map(i => i[valueKey]));
  el.innerHTML = `<div class="hbar-list">${items.map(i => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(i[labelKey])}">${escapeHtml(i[labelKey])}</span>
      <div class="hbar-track"><div class="hbar-fill ${colorClass}" style="width:${Math.max(4, (i[valueKey] / max) * 100)}%"></div></div>
      <span class="hbar-val">${i[valueKey]}</span>
    </div>
  `).join("")}</div>`;
}

function renderEmploymentChart(el, items) {
  if (!items || !items.length) { el.innerHTML = `<p class="chart-empty">No data yet.</p>`; return; }
  const max = Math.max(...items.map(i => i.total));
  el.innerHTML = `<div class="hbar-list">${items.map(i => `
    <div class="hbar-row">
      <span class="hbar-label" title="${escapeHtml(i.label)}">${escapeHtml(i.label)}</span>
      <div class="hbar-track hbar-track-stacked">
        <div class="hbar-fill dim-fill" style="width:${Math.max(4, (i.total / max) * 100)}%">
          <div class="hbar-fill-inner fraud-fill" style="width:${i.total ? (i.fraud / i.total) * 100 : 0}%"></div>
        </div>
      </div>
      <span class="hbar-val">${i.fraud}/${i.total}</span>
    </div>
  `).join("")}</div>
  <div class="chart-legend"><span><i class="dot dim"></i>Total</span><span><i class="dot fraud"></i>Flagged fraudulent</span></div>`;
}

// ---------- Browser extension token (About tab) ----------
let extTokenLoaded = false;
$("ext-token-reveal").addEventListener("click", async () => {
  const field = $("ext-token-field");
  const status = $("ext-token-status");
  if (extTokenLoaded) { field.type = field.type === "password" ? "text" : "password"; return; }
  try {
    const res = await fetch("/api/account/token");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not load your token.");
    field.value = data.api_token;
    field.type = "text";
    extTokenLoaded = true;
    $("ext-token-reveal").textContent = "Hide";
    $("ext-token-copy").hidden = false;
    $("ext-token-regen").hidden = false;
  } catch (err) {
    status.textContent = err.message;
  }
});
$("ext-token-copy").addEventListener("click", () => {
  navigator.clipboard.writeText($("ext-token-field").value);
  $("ext-token-status").textContent = "Copied to clipboard.";
});
$("ext-token-regen").addEventListener("click", async () => {
  if (!confirm("Regenerate your token? The extension will need the new one — any device using the old token will stop working.")) return;
  const res = await fetch("/api/account/token", { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    $("ext-token-field").value = data.api_token;
    $("ext-token-status").textContent = "Regenerated. Paste this into the extension popup.";
  } else {
    $("ext-token-status").textContent = data.error || "Could not regenerate token.";
  }
});

// ---------- Deep link from the extension: /?open_scan=<id> ----------
(async function openScanFromQueryString() {
  const params = new URLSearchParams(window.location.search);
  const scanId = params.get("open_scan");
  if (!scanId) return;
  history.replaceState({}, "", window.location.pathname); // strip the param so a refresh doesn't reopen it
  tabs.forEach(t => t.classList.remove("active"));
  panels.forEach(p => p.classList.remove("active"));
  document.querySelector('.tab[data-tab="history"]').classList.add("active");
  $("panel-history").classList.add("active");
  renderHistory();
  try {
    const res = await fetch(`/api/scans/${scanId}`);
    const entry = await res.json();
    if (res.ok) openReportModal(entry);
  } catch { /* silently ignore — the History tab is already open as a fallback */ }
})();

// ---------- Batch scan ----------
let batchRows = [];

function csvSafe(value) {
  // Stop spreadsheet formula injection (=, +, -, @) and quote properly.
  let v = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(v)) v = "'" + v;
  return '"' + v.replace(/"/g, '""') + '"';
}
function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

$("batch-template-btn").addEventListener("click", () => {
  downloadText("scamurai-batch-template.csv",
    "title,company_profile,description,requirements,benefits,salary_range,employment_type,telecommuting,has_company_logo\n" +
    '"Data Entry Executive","","Work from home and earn 5000 per day. Pay a small registration fee. Contact on WhatsApp.","","","","Part-time",1,0\n' +
    '"Backend Engineer","Acme builds payment infrastructure.","Design and maintain scalable services.","3+ years Python","Health insurance","","Full-time",0,1\n');
});

function renderBatchRows() {
  const filter = $("batch-filter").value;
  const rows = batchRows.filter(r => filter === "ALL" || r.risk_level === filter);
  $("batch-body").innerHTML = rows.map(r => {
    const verdictCls = r.label === "FRAUDULENT" ? "fraud" : (r.label === "SKIPPED" ? "" : "legit");
    const prob = r.fraud_probability == null ? "—" : r.fraud_probability.toFixed(1) + "%";
    const flags = r.red_flags.length ? r.red_flags.map(escapeHtml).join("; ") : (r.note ? escapeHtml(r.note) : "—");
    return `<tr>
      <td>${r.row}</td>
      <td>${escapeHtml(r.title || "—")}</td>
      <td><span class="hist-verdict ${verdictCls}">${escapeHtml(r.label)}</span></td>
      <td>${prob}</td>
      <td>${escapeHtml(r.risk_level)}</td>
      <td>${flags}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6">No rows match this filter.</td></tr>`;
}

$("batch-filter").addEventListener("change", renderBatchRows);

$("batch-export-btn").addEventListener("click", () => {
  const header = ["row", "title", "verdict", "fraud_probability_pct", "risk_level", "red_flags"];
  const lines = [header.join(",")].concat(batchRows.map(r =>
    [r.row, r.title, r.label, r.fraud_probability == null ? "" : r.fraud_probability,
     r.risk_level, r.red_flags.join("; ")].map(csvSafe).join(",")));
  downloadText("scamurai-batch-results.csv", lines.join("\r\n"));
});

$("batch-run-btn").addEventListener("click", async () => {
  const fileInput = $("batch-file"), errEl = $("batch-error"), btn = $("batch-run-btn");
  errEl.hidden = true;
  if (!fileInput.files.length) {
    errEl.textContent = "Choose a CSV file first.";
    errEl.hidden = false;
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  if ($("batch-save").checked) form.append("save", "1");
  btn.disabled = true;
  const oldLabel = btn.textContent;
  btn.textContent = "Scanning…";
  try {
    const res = await fetch("/api/batch-scan", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) {
      errEl.textContent = data.error || `Scan failed (HTTP ${res.status}).`;
      errEl.hidden = false;
      return;
    }
    batchRows = data.rows;
    const s = data.summary;
    $("batch-summary").innerHTML = [
      [s.scored, "Rows scanned"], [s.fraudulent, "Flagged fraudulent"], [s.high_risk, "High risk"],
      [s.with_red_flags, "With red flags"], [s.skipped, "Skipped (empty)"],
    ].map(([v, l]) => `<div class="metric-tile"><div class="val">${v}</div><div class="lbl">${l}</div></div>`).join("");
    $("batch-summary").hidden = false;
    $("batch-results-wrap").hidden = false;
    $("batch-filter").value = "ALL";
    renderBatchRows();
  } catch (e) {
    errEl.textContent = "Could not reach the server. Is the app running?";
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = oldLabel;
  }
});
