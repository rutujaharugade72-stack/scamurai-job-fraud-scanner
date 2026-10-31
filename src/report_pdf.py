
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table,
    TableStyle, KeepTogether, HRFlowable,
)
from reportlab.graphics.shapes import Drawing, Rect, String

# ---------------------------------------------------------------- palette
INK = colors.HexColor("#161b26")
INK_DIM = colors.HexColor("#5b6478")
BRAND = colors.HexColor("#c98a2e")
BRAND_LIGHT = colors.HexColor("#f7ecd9")
LEGIT = colors.HexColor("#1f9d76")
LEGIT_BG = colors.HexColor("#e6f7f1")
FRAUD = colors.HexColor("#d64358")
FRAUD_BG = colors.HexColor("#fbe9ec")
LINE = colors.HexColor("#e2e5ec")
PAGE_W, PAGE_H = A4

FIELD_LABELS = [
    ("title", "Job title"),
    ("company_profile", None),  # handled separately (long text)
    ("employment_type", "Employment type"),
    ("required_experience", "Required experience"),
    ("required_education", "Required education"),
    ("industry", "Industry"),
    ("function", "Job function"),
    ("salary_range", "Salary range"),
    ("location", "Location"),
]

BOOL_FIELD_LABELS = [
    ("has_company_logo", "Company logo shown"),
    ("telecommuting", "Telecommuting / remote"),
    ("has_questions", "Screening questions present"),
]


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("ReportTitle", parent=ss["Title"], fontName="Helvetica-Bold",
                           fontSize=19, leading=23, textColor=INK, spaceAfter=2))
    ss.add(ParagraphStyle("ReportSubtitle", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, textColor=INK_DIM, spaceAfter=0))
    ss.add(ParagraphStyle("Meta", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=8.5, textColor=INK_DIM, alignment=TA_LEFT, leading=12))
    ss.add(ParagraphStyle("SectionHeading", parent=ss["Heading2"], fontName="Helvetica-Bold",
                           fontSize=12.5, textColor=INK, spaceBefore=14, spaceAfter=6,
                           borderPadding=0))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=14, textColor=INK, alignment=TA_LEFT))
    ss.add(ParagraphStyle("BodyDim", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9, leading=13, textColor=INK_DIM))
    ss.add(ParagraphStyle("CellLabel", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=8.5, textColor=INK_DIM, leading=11))
    ss.add(ParagraphStyle("CellValue", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, textColor=INK, leading=13))
    ss.add(ParagraphStyle("VerdictBig", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=16, leading=19, alignment=TA_CENTER))
    ss.add(ParagraphStyle("VerdictSmall", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9, alignment=TA_CENTER))
    ss.add(ParagraphStyle("ChipFraud", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=8.5, textColor=FRAUD, leading=16))
    ss.add(ParagraphStyle("ChipLegit", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=8.5, textColor=LEGIT, leading=16))
    ss.add(ParagraphStyle("Disclaimer", parent=ss["Normal"], fontName="Helvetica-Oblique",
                           fontSize=8, textColor=INK_DIM, leading=12))
    return ss


def _p(text, style):
    return Paragraph(escape(str(text)) if text not in (None, "") else "—", style)


def _clean(text):
    return escape(str(text)) if text not in (None, "") else ""


def _bool_word(v):
    return "Yes" if v else "No"


def _probability_row(label, pct, color, width=380, height=14):
    """One labeled, always-legible row: name + track/fill bar + exact
    percentage. Two of these (legit/fraud) replace the old single stacked
    bar, whose minority-class label used to disappear below ~7% because
    the segment was too thin to fit text — exactly the case (e.g. 99/1)
    where the reader most wants to see the precise minority number."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#eef0f4"), strokeColor=None))
    d.add(Rect(0, 0, width * max(min(pct, 100), 0) / 100.0, height, fillColor=color, strokeColor=None))
    label_style = ParagraphStyle("prl", fontName="Helvetica-Bold", fontSize=9, textColor=INK)
    pct_style = ParagraphStyle("prp", fontName="Helvetica-Bold", fontSize=9, textColor=color, alignment=TA_RIGHT)
    row = Table([[Paragraph(label, label_style), d, Paragraph(f"{pct:.1f}%", pct_style)]],
                colWidths=[80, width, 50])
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return row


def _confidence_bar(pct, color, width=460, height=10):
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#eef0f4"), strokeColor=None))
    d.add(Rect(0, 0, width * (pct / 100.0), height, fillColor=color, strokeColor=None))
    return d


def _chip_row(words, chip_style, empty_text):
    if not words:
        return Paragraph(f'<i>{empty_text}</i>', ParagraphStyle(
            "empty", fontName="Helvetica-Oblique", fontSize=8.5, textColor=INK_DIM))
    joined = "  &middot;  ".join(escape(w) for w in words)
    return Paragraph(joined, chip_style)


def _impact_bar(delta_pp, max_abs, width=250, height=10):
    """Diverging bar centred at zero: grows right (red) for a swing toward
    FRAUDULENT, left (green) for a swing toward LEGITIMATE."""
    d = Drawing(width, height)
    center = width / 2
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor("#eef0f4"), strokeColor=None))
    frac = min(abs(delta_pp) / max_abs, 1.0) if max_abs else 0
    bar_w = (width / 2) * frac
    bar_color = FRAUD if delta_pp > 0 else LEGIT
    if delta_pp > 0:
        d.add(Rect(center, 0, bar_w, height, fillColor=bar_color, strokeColor=None))
    else:
        d.add(Rect(center - bar_w, 0, bar_w, height, fillColor=bar_color, strokeColor=None))
    d.add(Rect(center - 0.5, 0, 1, height, fillColor=colors.HexColor("#9aa0b0"), strokeColor=None))
    return d


def _structured_impact_table(impacts, styles):
    if not impacts:
        return Paragraph(
            "<i>No individual structured field moved the prediction by more than the reporting threshold.</i>",
            styles["BodyDim"])
    max_abs = max(abs(i["delta_pp"]) for i in impacts) or 1.0
    rows = []
    for i in impacts:
        sign = "+" if i["delta_pp"] > 0 else ""
        color = FRAUD if i["direction"] == "fraud" else LEGIT
        label_cell = Paragraph(
            f'<b>{escape(i["label"])}</b><br/><font size="7.5" color="#5b6478">{escape(str(i["value"]))}</font>',
            styles["CellValue"])
        delta_style = ParagraphStyle("d", fontName="Helvetica-Bold", fontSize=9, textColor=color, alignment=TA_CENTER)
        rows.append([label_cell, _impact_bar(i["delta_pp"], max_abs), Paragraph(f'{sign}{i["delta_pp"]:.1f} pp', delta_style)])
    t = Table(rows, colWidths=[168, 262, 64])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]))
    return t


def _details_table(record, styles):
    rows = []
    for key, label in FIELD_LABELS:
        if label is None:
            continue
        val = record.get(key)
        if key in ("employment_type", "required_experience", "required_education", "industry", "function"):
            val = val if val and val != "Unknown" else "Not specified"
        if val in (None, "", "Not specified"):
            continue
        rows.append([Paragraph(label, styles["CellLabel"]), Paragraph(_clean(val) or "—", styles["CellValue"])])
    for key, label in BOOL_FIELD_LABELS:
        rows.append([Paragraph(label, styles["CellLabel"]), Paragraph(_bool_word(record.get(key)), styles["CellValue"])])

    # lay out as a 2-column-of-pairs grid (4 columns total) for compactness
    grid = []
    for i in range(0, len(rows), 2):
        pair = rows[i:i + 2]
        flat = pair[0] + (pair[1] if len(pair) > 1 else [Paragraph("", styles["CellValue"]), Paragraph("", styles["CellValue"])])
        grid.append(flat)

    t = Table(grid, colWidths=[85, 145, 85, 145])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LEFTPADDING", (2, 0), (2, -1), 12),
    ]))
    return t


def _long_text_block(heading, text, styles):
    if not (text or "").strip():
        return None
    text = text.strip()
    return KeepTogether([
        Paragraph(heading, styles["SectionHeading"]),
        Paragraph(_clean(text).replace("\n", "<br/>"), styles["Body"]),
        Spacer(1, 4),
    ])


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(18 * mm, 15 * mm, PAGE_W - 18 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(INK_DIM)
    canvas.drawString(18 * mm, 11 * mm, "Sentinel — AI Job Posting Fraud Scanner  ·  Automated report, not legal or professional advice")
    canvas.drawRightString(PAGE_W - 18 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _verification_block(verification, styles):
    """Render the external verification section. `verification` is the
    /api/verify response dict, or None if it wasn't run for this scan."""
    if not verification:
        return Paragraph("<i>Not run for this scan.</i>", styles["BodyDim"])
    if verification.get("nothing_to_check"):
        return Paragraph(
            "<i>No company website or recruiter email address was found in the posting text to check.</i>",
            styles["BodyDim"])

    flags = verification.get("verification_flags") or []
    blocks = []
    if flags:
        items = [[Paragraph(f'⚠ {escape(f)}', ParagraphStyle(
            "vf", parent=styles["Body"], textColor=FRAUD))] for f in flags]
        t = Table(items, colWidths=[494])
        t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        blocks.append(t)
    else:
        n_d = len(verification.get("domains_found") or [])
        n_e = len(verification.get("emails_found") or [])
        blocks.append(Paragraph(
            f"✓ No issues found across {n_d} domain(s) and {n_e} email(s) checked.",
            ParagraphStyle("vok", parent=styles["Body"], textColor=LEGIT)))

    rows = []
    for d in verification.get("domain_checks") or []:
        whois_info = d.get("whois") or {}
        if whois_info.get("age_days") is not None:
            age = f'{whois_info["age_days"]} days old (registered {whois_info.get("created")})'
        else:
            age = whois_info.get("error") or "age unknown"
        reach = "reachable" if d.get("reachable") is True else ("unreachable" if d.get("reachable") is False else "unknown")
        rows.append([Paragraph(d["domain"], styles["CellValue"]), Paragraph(f"{reach} · {age}", styles["BodyDim"])])
    for e in verification.get("email_checks") or []:
        mx = "has mail server" if e.get("has_mx") is True else ("no mail server" if e.get("has_mx") is False else "MX unknown")
        kind = "free provider" if e.get("is_free_provider") else "company domain"
        rows.append([Paragraph(e["email"], styles["CellValue"]), Paragraph(f"{kind} · {mx}", styles["BodyDim"])])

    if rows:
        t2 = Table(rows, colWidths=[220, 274])
        t2.setStyle(TableStyle([
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ]))
        blocks.append(Spacer(1, 4))
        blocks.append(t2)

    return KeepTogether(blocks)


def build_report_pdf(record: dict, result: dict, meta: dict | None = None, verification: dict | None = None) -> bytes:
    """record: the job-posting fields the user submitted (title, description, ...).
    result: the /api/predict response (prediction, confidence, risk_factors, ...).
    meta: optional {report_id, generated_at, source_url, extraction_method}.
    verification: optional /api/verify response for this posting.
    Returns raw PDF bytes."""
    meta = meta or {}
    styles = _styles()
    buf = BytesIO()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
        title=f"Sentinel Fraud Report — {record.get('title') or 'Job Posting'}",
        author="Sentinel Job Posting Fraud Scanner",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=_footer)])

    story = []

    # ---- Header -----------------------------------------------------
    generated_at = meta.get("generated_at") or datetime.now().strftime("%d %b %Y, %I:%M %p")
    report_id = meta.get("report_id") or datetime.now().strftime("SNT-%Y%m%d-%H%M%S")

    header_tbl = Table(
        [[Paragraph("◈ SENTINEL", ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=13, textColor=BRAND)),
          Paragraph(f"Report ID: {report_id}<br/>Generated: {generated_at}", styles["Meta"])]],
        colWidths=[300, 194],
    )
    header_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story.append(header_tbl)
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.4, color=BRAND, spaceAfter=10))

    story.append(Paragraph("Job Posting Fraud Analysis Report", styles["ReportTitle"]))
    story.append(Paragraph(
        "Generated by an automated machine-learning classifier trained on the EMSCAD job-postings dataset.",
        styles["ReportSubtitle"]))
    story.append(Spacer(1, 12))

    # ---- Job identity line -------------------------------------------
    title = record.get("title") or "(untitled posting)"
    company_line = record.get("company_profile") or ""
    company_name = company_line.split("—")[0].strip() if company_line else ""
    identity_bits = [f'<b>{escape(title)}</b>']
    if company_name:
        identity_bits.append(escape(company_name))
    if meta.get("source_url"):
        identity_bits.append(f'<font color="#5b6478" size="8">Source: {escape(meta["source_url"].split("?")[0])}</font>')
    story.append(Paragraph("<br/>".join(identity_bits), styles["Body"]))
    story.append(Spacer(1, 10))

    # ---- Verdict box ---------------------------------------------------
    is_fraud = int(result.get("prediction", 0)) == 1
    verdict_bg = FRAUD_BG if is_fraud else LEGIT_BG
    verdict_fg = FRAUD if is_fraud else LEGIT
    verdict_text = "LIKELY FRAUDULENT" if is_fraud else "LIKELY LEGITIMATE"

    verdict_style = ParagraphStyle("verdict", parent=styles["VerdictBig"], textColor=verdict_fg)
    conf_style = ParagraphStyle("conf", parent=styles["VerdictSmall"], textColor=verdict_fg)

    verdict_cell = [
        Paragraph(verdict_text, verdict_style),
        Paragraph(f"Model confidence: {result.get('confidence', 0)}%  ·  Model used: {escape(str(result.get('model_used', '—')))}", conf_style),
    ]
    verdict_tbl = Table([[verdict_cell]], colWidths=[494])
    verdict_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), verdict_bg),
        ("BOX", (0, 0), (-1, -1), 0.75, verdict_fg),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(verdict_tbl)
    story.append(Spacer(1, 12))

    # ---- Probability breakdown -----------------------------------------
    story.append(Paragraph("Probability Breakdown", styles["SectionHeading"]))
    story.append(_probability_row("Legitimate", float(result.get("legit_probability", 0)), LEGIT))
    story.append(Spacer(1, 4))
    story.append(_probability_row("Fraudulent", float(result.get("fraud_probability", 0)), FRAUD))
    story.append(Spacer(1, 6))

    # ---- External verification ------------------------------------
    story.append(Paragraph("External Verification", styles["SectionHeading"]))
    story.append(Paragraph(
        "Live checks against the company domain(s) and recruiter email address(es) mentioned in the "
        "posting: website reachability, domain-registration age (WHOIS), and mail-server (MX) presence.",
        styles["BodyDim"]))
    story.append(Spacer(1, 4))
    story.append(_verification_block(verification, styles))
    story.append(Spacer(1, 6))

    # ---- Structured-field impact (counterfactual ablation) -------------
    story.append(Paragraph("Structured-Field Impact", styles["SectionHeading"]))
    story.append(Paragraph(
        "Each field below was reset to its neutral baseline one at a time (posting text held fixed) "
        "and re-scored against the deployed model; the bar shows how many percentage points that single "
        "field moved the fraud probability for this specific posting.",
        styles["BodyDim"]))
    story.append(Spacer(1, 4))
    story.append(_structured_impact_table(result.get("structured_impact") or [], styles))
    story.append(Spacer(1, 6))

    # ---- Risk signals ----------------------------------------------
    risk_factors = result.get("risk_factors") or []
    positive_factors = result.get("positive_factors") or []
    if risk_factors:
        story.append(Paragraph("Heuristic Risk Signals", styles["SectionHeading"]))
        items = [[Paragraph(f"•  {escape(rf)}", styles["Body"])] for rf in risk_factors]
        rt = Table(items, colWidths=[494])
        rt.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(rt)
    elif positive_factors:
        story.append(Paragraph("Why This Looks Legitimate",
                                ParagraphStyle("h", parent=styles["SectionHeading"], textColor=LEGIT)))
        items = [[Paragraph(f"•  {escape(pf)}", styles["Body"])] for pf in positive_factors]
        rt = Table(items, colWidths=[494])
        rt.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(rt)
    else:
        story.append(Paragraph("Heuristic Risk Signals", styles["SectionHeading"]))
        story.append(Paragraph("<i>No heuristic risk signals were triggered for this posting.</i>", styles["BodyDim"]))
    story.append(Spacer(1, 4))

    # ---- Explainability ----------------------------------------------
    story.append(Paragraph("Model Explainability — Influential Terms", styles["SectionHeading"]))
    exp_tbl = Table([
        [Paragraph("Pushed toward FRAUDULENT", ParagraphStyle("h", parent=styles["CellLabel"], textColor=FRAUD)),
         Paragraph("Pushed toward LEGITIMATE", ParagraphStyle("h", parent=styles["CellLabel"], textColor=LEGIT))],
        [_chip_row(result.get("fraud_words"), styles["ChipFraud"], "No strong fraud-indicating terms found"),
         _chip_row(result.get("legit_words"), styles["ChipLegit"], "No strong legitimacy-indicating terms found")],
    ], colWidths=[247, 247])
    exp_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
    ]))
    story.append(exp_tbl)
    method = result.get("explanation_method")
    if method == "shap-partition-text":
        base = result.get("explanation_base_value")
        base_txt = f" &middot; background baseline fraud probability: {base * 100:.1f}%" if base is not None else ""
        story.append(Paragraph(
            f"Word attributions computed with SHAP (Partition / text-masking explainer) — exact, "
            f"model-agnostic Shapley values, not a linear-model approximation{base_txt}.",
            styles["Disclaimer"]))
    elif method == "lr-coefficient-fallback":
        story.append(Paragraph(
            "SHAP was unavailable in the serving environment; word attributions fall back to a "
            "Logistic-Regression-coefficient approximation.",
            styles["Disclaimer"]))
    story.append(Spacer(1, 6))

    # ---- Job details table ----------------------------------------------
    story.append(Paragraph("Submitted Job Posting Details", styles["SectionHeading"]))
    story.append(_details_table(record, styles))
    story.append(Spacer(1, 6))

    if meta.get("extraction_method"):
        story.append(Paragraph(f"Import method: {escape(meta['extraction_method'])}", styles["BodyDim"]))
        story.append(Spacer(1, 6))

    # ---- Full text fields ----------------------------------------------
    for heading, key in [
        ("Company Profile", "company_profile"),
        ("Job Description", "description"),
        ("Requirements", "requirements"),
        ("Benefits", "benefits"),
    ]:
        block = _long_text_block(heading, record.get(key), styles)
        if block:
            story.append(block)

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceAfter=8))
    story.append(Paragraph(
        "Disclaimer: this report is generated by a statistical machine-learning model and reflects a "
        "probabilistic pattern match against historical fraudulent and legitimate job postings — it is "
        "not proof of fraud or legitimacy and should not be treated as a final determination. Always "
        "independently verify the employer's official domain, registration, and contact details, and "
        "never pay money or share sensitive personal/financial information as part of a job application.",
        styles["Disclaimer"]))

    doc.build(story)
    return buf.getvalue()
