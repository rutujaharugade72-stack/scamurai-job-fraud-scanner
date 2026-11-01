

(function () {
  if (window.__sentinelInjected) return;
  window.__sentinelInjected = true;

  // ---------------------------------------------------------------
  // 1. Extraction — prefer the page's own JobPosting JSON-LD (same
  //    schema.org data Google uses for "Jobs" search results); it's
  //    clean and structured. Fall back to a generic "largest text
  //    block" scrape when a page doesn't publish it.
  // ---------------------------------------------------------------
  function findJobPostingJsonLd() {
    const tags = document.querySelectorAll('script[type="application/ld+json"]');
    for (const tag of tags) {
      let data;
      try { data = JSON.parse(tag.textContent); } catch { continue; }
      const candidates = Array.isArray(data) ? data : [data];
      for (const c of candidates) {
        if (c && c["@graph"]) candidates.push(...c["@graph"]);
      }
      for (const item of candidates) {
        if (item && (item["@type"] === "JobPosting" ||
            (Array.isArray(item["@type"]) && item["@type"].includes("JobPosting")))) {
          return item;
        }
      }
    }
    return null;
  }

  function extractFallbackText() {
    const blocks = Array.from(document.querySelectorAll(
      "main p, article p, main li, article li, main h2, main h3, main h4, article h2, article h3, article h4, [class*='description'] p, [class*='description'] li"
    ));
    let text = blocks.map(el => el.innerText.trim()).filter(t => t.length > 5).join("\n");
    if (text.length < 150) {
      // last resort: any paragraph/list item/heading on the page
      text = Array.from(document.querySelectorAll("p, li, h2, h3, h4")).map(el => el.innerText.trim())
        .filter(t => t.length > 5).join("\n").slice(0, 8000);
    }
    return `${document.title}\n\n${text}`.slice(0, 9000);
  }

  // ---------------------------------------------------------------
  // 1b. DOM enrichment — some page chrome (LinkedIn's "About the
  //     company" panel and its logo, for example) is rendered by the
  //     host site's own widgets, entirely separate from the JobPosting
  //     JSON-LD or the main article text. JSON-LD / text extraction can
  //     legitimately miss it even though it's clearly visible on screen.
  //     Since this script runs inside the real, logged-in, fully
  //     rendered page, we can just read it straight off the DOM instead
  //     of relying on the job-description text alone.
  // ---------------------------------------------------------------
  function getCompanyNameFromDom(jsonld) {
    if (jsonld && jsonld.hiringOrganization) {
      const org = Array.isArray(jsonld.hiringOrganization) ? jsonld.hiringOrganization[0] : jsonld.hiringOrganization;
      if (org && org.name) return org.name;
    }
    // A link to a /company/ page is a stable pattern (LinkedIn, and many
    // ATS-hosted career pages) regardless of how obfuscated the CSS classes are.
    const orgLink = document.querySelector('a[href*="/company/"]');
    if (orgLink && orgLink.textContent.trim()) return orgLink.textContent.trim();
    return "";
  }

  function extractAboutCompanySection() {
    const headingEls = Array.from(document.querySelectorAll("h1, h2, h3, h4, span, strong"));
    const heading = headingEls.find(el => {
      const t = el.textContent.trim().toLowerCase();
      return t === "about the company" || t === "about us" || t === "company overview";
    });
    if (!heading) return "";

    let container = heading.parentElement;
    let text = "";
    for (let i = 0; i < 6 && container; i++) {
      text = (container.innerText || "").trim();
      if (text.length > heading.textContent.trim().length + 60) break;
      container = container.parentElement;
    }
    if (!text) return "";

    text = text.replace(/about the company/i, "").replace(/^\s*follow\s*/im, "");
    text = text.split(/\bshow more\b/i)[0];
    // drop follower/size stats, bullets and "… more" markers (server also re-cleans)
    text = text.split("\n").map(l => l.trim()).filter(l =>
      l && !/^(•|·)$/.test(l) && !/^[\d,.]+[kKmM]?\+?\s+(followers|on linkedin)$/i.test(l) &&
      !/^[\d,]+(-[\d,]+|\+)?\s+employees$/i.test(l) && !/^(…|\.{3})\s*more$/i.test(l)).join("\n");
    return text.replace(/\s*(…|\.{3})\s*more\s*$/i, "").trim().slice(0, 6000);
  }

  function detectCompanyLogo(companyName) {
    const orgLink = document.querySelector('a[href*="/company/"]');
    if (orgLink) {
      if (orgLink.querySelector("img[src]")) return true;
      const wrap = orgLink.closest("div, section, li");
      if (wrap && wrap.querySelector("img[src]:not([src=''])")) return true;
    }
    const nameLower = (companyName || "").toLowerCase();
    return Array.from(document.querySelectorAll("img[src]")).some(img => {
      const src = img.getAttribute("src") || "";
      if (!src || src.startsWith("data:")) return false;
      const alt = (img.alt || "").toLowerCase();
      if (alt.includes("logo")) return true;
      if (nameLower && alt && alt.includes(nameLower)) return true;
      const cls = (img.className || "") + " " + (img.closest("[class]")?.className || "");
      if (/logo/i.test(cls)) return true;
      return false;
    });
  }

  function detectWorkplaceTypeBadge() {
    // LinkedIn (and many ATS pages) show an explicit "On-site" / "Hybrid" /
    // "Remote" pill right next to the job title — that's the authoritative
    // signal, not a keyword guess off the description text. Scope the
    // search to the main job-details panel so we don't pick up an
    // unrelated badge from a different job card in a list sidebar.
    const scope = document.querySelector("main") || document.body;
    const candidates = Array.from(scope.querySelectorAll("span, button, li, div"))
      .filter(el => el.children.length === 0); // leaf nodes only — the badge's own text, not a wrapper
    for (const el of candidates) {
      const t = el.textContent.trim().toLowerCase();
      if (t === "remote") return "remote";
      if (t === "hybrid") return "hybrid";
      if (t === "on-site" || t === "onsite") return "onsite";
    }
    return "";
  }

  function enrichRecordFromDom(record, jsonld) {
    const companyName = getCompanyNameFromDom(jsonld);
    const aboutText = extractAboutCompanySection();
    if (aboutText && aboutText.length > (record.company_profile || "").length) {
      record.company_profile = companyName ? `${companyName} — ${aboutText}` : aboutText;
    } else if (companyName && !record.company_profile) {
      record.company_profile = companyName;
    }
    if (detectCompanyLogo(companyName)) {
      record.has_company_logo = 1;
    }
    // Explicit workplace-type badge wins outright over any keyword guess
    // the server-side text parser already made (e.g. "on-site" role whose
    // description happens to mention "remote stakeholders").
    const workplaceType = detectWorkplaceTypeBadge();
    if (workplaceType === "remote") {
      record.telecommuting = 1;
    } else if (workplaceType === "onsite" || workplaceType === "hybrid") {
      record.telecommuting = 0;
    }
    return record;
  }

  // ---------------------------------------------------------------
  // 2. Messaging helpers
  // ---------------------------------------------------------------
  function send(message) {
    return new Promise(resolve => chrome.runtime.sendMessage(message, resolve));
  }

  // ---------------------------------------------------------------
  // 3. Floating action button + overlay panel, in a shadow root so the
  //    host page's CSS can never bleed into Sentinel's UI or vice versa.
  // ---------------------------------------------------------------
  const host = document.createElement("div");
  host.id = "sentinel-ext-host";
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });

  const styleTag = document.createElement("style");
  styleTag.textContent = `
    :host, * { box-sizing: border-box; font-family: -apple-system, "Segoe UI", Inter, Roboto, sans-serif; }
    .sentinel-fab {
      position: fixed; bottom: 24px; right: 24px; z-index: 2147483000;
      background: #10131c; color: #f2a83c; border: 1px solid #2a3145;
      padding: 12px 18px; border-radius: 999px; font-size: 13.5px; font-weight: 600;
      cursor: pointer; box-shadow: 0 8px 24px rgba(0,0,0,0.35); display: flex; align-items: center; gap: 8px;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .sentinel-fab:hover { transform: translateY(-2px); box-shadow: 0 12px 28px rgba(0,0,0,0.45); }
    .sentinel-fab-mark { color: #f2a83c; font-size: 15px; }
    .sentinel-panel {
      position: fixed; bottom: 78px; right: 24px; z-index: 2147483000;
      width: 320px; max-height: 70vh; overflow-y: auto;
      background: #151a26; color: #e7e9f0; border: 1px solid #2a3145; border-radius: 14px;
      box-shadow: 0 16px 40px rgba(0,0,0,0.5); font-size: 13.5px;
    }
    .sentinel-head {
      display: flex; align-items: center; justify-content: space-between; padding: 14px 16px;
      border-bottom: 1px solid #2a3145; font-weight: 700; color: #f2a83c; font-size: 14px;
    }
    .sentinel-close { background: none; border: none; color: #9099b3; font-size: 18px; cursor: pointer; line-height: 1; }
    .sentinel-body { padding: 16px; }
    .sentinel-loading { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 24px 8px; color: #9099b3; text-align: center; }
    .sentinel-spinner { width: 22px; height: 22px; border: 2.5px solid #2a3145; border-top-color: #f2a83c; border-radius: 50%; animation: sentinel-spin 0.7s linear infinite; }
    @keyframes sentinel-spin { to { transform: rotate(360deg); } }
    .sentinel-error { color: #ef5a6f; margin: 0; line-height: 1.5; }
    .sentinel-verdict { font-weight: 700; font-size: 14.5px; padding: 10px 12px; border-radius: 8px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 2px; }
    .sentinel-verdict.legit { background: rgba(46,204,155,0.12); color: #2ecc9b; }
    .sentinel-verdict.fraud { background: rgba(239,90,111,0.12); color: #ef5a6f; }
    .sentinel-conf { font-weight: 500; font-size: 11.5px; opacity: 0.85; }
    .sentinel-bar { height: 6px; border-radius: 999px; background: #212739; overflow: hidden; margin-bottom: 4px; }
    .sentinel-bar-fill { height: 100%; background: #ef5a6f; }
    .sentinel-bar-label { margin: 0 0 12px; color: #9099b3; font-size: 11.5px; }
    .sentinel-risks { margin: 0 0 14px; padding-left: 18px; color: #c3c8d6; font-size: 12.5px; line-height: 1.6; }
    .sentinel-signals-label { margin: 0 0 6px; font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; }
    .sentinel-signals-label.sentinel-signals-risk { color: #ef5a6f; }
    .sentinel-signals-label.sentinel-signals-positive { color: #2ecc9b; }
    .sentinel-positives li::marker { color: #2ecc9b; }
    .sentinel-actions { display: flex; gap: 8px; }
    .sentinel-btn { flex: 1; padding: 9px 10px; border-radius: 8px; border: 1px solid #2a3145; background: #212739; color: #e7e9f0; font-size: 12.5px; font-weight: 600; cursor: pointer; }
    .sentinel-btn-primary { background: #f2a83c; color: #10131c; border-color: #f2a83c; }
    .sentinel-btn:disabled { opacity: 0.6; cursor: default; }
    .sentinel-hint { margin: 10px 0 0; color: #5c6584; font-size: 11px; line-height: 1.5; }
  `;
  shadow.appendChild(styleTag);

  const fab = document.createElement("button");
  fab.className = "sentinel-fab";
  fab.innerHTML = `<span class="sentinel-fab-mark">&#9670;</span> Scan with Sentinel`;
  shadow.appendChild(fab);

  const panel = document.createElement("div");
  panel.className = "sentinel-panel";
  panel.hidden = true;
  shadow.appendChild(panel);

  function renderPanel(html) {
    panel.innerHTML = html;
    panel.hidden = false;
    const closeBtn = panel.querySelector(".sentinel-close");
    if (closeBtn) closeBtn.addEventListener("click", () => { panel.hidden = true; });
  }

  function renderLoading(msg) {
    renderPanel(`
      <div class="sentinel-head">
        <span>&#9670; Sentinel</span>
        <button class="sentinel-close">&times;</button>
      </div>
      <div class="sentinel-body sentinel-loading">
        <div class="sentinel-spinner"></div>
        <p>${msg}</p>
      </div>
    `);
  }

  function renderError(msg) {
    renderPanel(`
      <div class="sentinel-head">
        <span>&#9670; Sentinel</span>
        <button class="sentinel-close">&times;</button>
      </div>
      <div class="sentinel-body">
        <p class="sentinel-error">${escapeHtml(msg)}</p>
      </div>
    `);
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  let lastRecord = null, lastResult = null, lastMeta = null;

  function renderResult(record, result, meta) {
    lastRecord = record; lastResult = result; lastMeta = meta;
    const isFraud = result.prediction === 1;
    const risk = (result.risk_factors || []).slice(0, 4);
    const positives = (result.positive_factors || []).slice(0, 4);
    const signalsHtml = risk.length
      ? `<p class="sentinel-signals-label sentinel-signals-risk">Risk signals</p>
         <ul class="sentinel-risks">${risk.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`
      : positives.length
        ? `<p class="sentinel-signals-label sentinel-signals-positive">Why this looks legitimate</p>
           <ul class="sentinel-risks sentinel-positives">${positives.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`
        : `<p class="sentinel-hint" style="margin-top:0;">No strong signals either way from the posting's metadata — this verdict is driven mainly by the wording of the description itself.</p>`;
    renderPanel(`
      <div class="sentinel-head">
        <span>&#9670; Sentinel</span>
        <button class="sentinel-close">&times;</button>
      </div>
      <div class="sentinel-body">
        <div class="sentinel-verdict ${isFraud ? "fraud" : "legit"}">
          ${isFraud ? "&#9888; Likely Fraudulent" : "&#10003; Likely Legitimate"}
          <span class="sentinel-conf">${result.confidence}% confidence</span>
        </div>
        <div class="sentinel-bar">
          <div class="sentinel-bar-fill" style="width:${result.fraud_probability}%"></div>
        </div>
        <p class="sentinel-bar-label">${result.fraud_probability}% fraud probability</p>
        ${signalsHtml}
        <div class="sentinel-actions">
          <button class="sentinel-btn sentinel-btn-primary" id="sentinel-save-btn">Save &amp; view full report</button>
        </div>
        <p class="sentinel-hint">Full report, PDF export, and explainability breakdown open in Sentinel.</p>
      </div>
    `);
    const saveBtn = panel.querySelector("#sentinel-save-btn");
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
      const saveRes = await send({ type: "SAVE_SCAN", record: lastRecord, result: lastResult, meta: lastMeta });
      if (!saveRes.ok) {
        renderError(saveRes.data?.error || "Could not save this scan to Sentinel.");
        return;
      }
      await send({ type: "OPEN_SCAN", scanId: saveRes.data.id });
      panel.hidden = true;
    });
  }

  async function runScan() {
    renderLoading("Reading the job posting on this page...");

    const jsonld = findJobPostingJsonLd();
    let importRes;
    if (jsonld) {
      importRes = await send({ type: "IMPORT_JSONLD", jsonld, url: location.href });
    } else {
      importRes = await send({ type: "IMPORT_TEXT", text: extractFallbackText() });
    }
    if (!importRes.ok || !importRes.data.success) {
      renderError(importRes.data?.error || "Couldn't read this job posting. Try the 'Paste posting text' option in the Sentinel web app instead.");
      return;
    }
    const record = importRes.data.job;
    if (!record.source_url) record.source_url = location.href;
    enrichRecordFromDom(record, jsonld);

    renderLoading(`Scanning "${record.title || "this posting"}"...`);
    const predictRes = await send({ type: "PREDICT", record });
    if (!predictRes.ok) {
      renderError(predictRes.data?.error || "Scan failed.");
      return;
    }
    renderResult(record, predictRes.data, { source_url: record.source_url, extraction_method: record.extraction_method });
  }

  fab.addEventListener("click", runScan);
})();

