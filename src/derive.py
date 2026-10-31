
import re

# ---- Category vocabularies (top values from the training dataset) ----
EMPLOYMENT_TYPES = ["Full-time", "Part-time", "Contract", "Temporary", "Other"]

EXPERIENCE_LEVELS = [
    "Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive",
]

EDUCATION_LEVELS = [
    "High School or equivalent", "Certification", "Some College Coursework Completed",
    "Vocational", "Associate Degree", "Bachelor's Degree", "Master's Degree", "Doctorate",
]

INDUSTRY_KEYWORDS = {
    "Information Technology and Services": ["it services", "information technology", "tech consulting"],
    "Computer Software": ["software", "saas", "app development"],
    "Internet": ["internet company", "online platform", "e-commerce", "ecommerce"],
    "Marketing and Advertising": ["marketing agency", "advertising", "digital marketing"],
    "Education Management": ["university", "school district", "education management", "academy"],
    "Financial Services": ["bank", "financial services", "fintech", "investment firm"],
    "Hospital & Health Care": ["hospital", "health care", "healthcare", "clinic", "medical center"],
    "Telecommunications": ["telecom", "telecommunications"],
    "Oil & Energy": ["oil and gas", "energy sector", "petroleum"],
    "Retail": ["retail store", "retailer"],
    "Real Estate": ["real estate"],
    "Accounting": ["accounting firm", "cpa firm"],
    "Construction": ["construction company", "contractor"],
    "Insurance": ["insurance company", "insurer"],
    "Automotive": ["automotive", "car manufacturer"],
    "Logistics and Supply Chain": ["logistics", "supply chain", "freight"],
    "Human Resources": ["staffing agency", "recruiting firm", "hr consultancy"],
    "Legal Services": ["law firm", "legal services"],
}

FUNCTION_KEYWORDS = {
    "Information Technology": ["software engineer", "developer", "devops", "system administrator", "it support", "backend", "frontend", "full stack", "programmer"],
    "Sales": ["sales representative", "account executive", "business development rep", "sales manager"],
    "Engineering": ["mechanical engineer", "civil engineer", "electrical engineer", "engineering team"],
    "Customer Service": ["customer service", "customer support", "support representative"],
    "Marketing": ["marketing manager", "content marketing", "seo specialist", "social media manager"],
    "Administrative": ["administrative assistant", "office manager", "data entry"],
    "Design": ["ux designer", "ui designer", "graphic designer", "product designer"],
    "Health Care Provider": ["nurse", "physician", "therapist", "medical assistant"],
    "Management": ["general manager", "operations manager", "team lead"],
    "Human Resources": ["hr generalist", "recruiter", "talent acquisition", "human resources"],
    "Finance": ["financial analyst", "accountant", "controller"],
    "Data Analyst": ["data analyst", "data scientist", "business intelligence"],
    "Project Management": ["project manager", "program manager", "scrum master"],
}

REQUIREMENT_HEADERS = [
    "requirements", "qualifications", "what you'll need", "what you will need",
    "who you are", "skills required", "required skills",
    "minimum qualifications", "what we're looking for",
    "what you'll bring", "what you will bring",
]
BENEFIT_HEADERS = [
    "benefits", "perks", "what we offer", "why join us", "compensation",
    "what's in it for you", "we offer", "salary and benefits",
]
COMPANY_HEADERS = [
    "about us", "about the company", "who we are", "company overview", "our company",
    "about the team",
]
RESPONSIBILITY_HEADERS = [
    "responsibilities", "what you'll do", "job description", "duties",
]
# Headers that mark the START of trailing junk on job-board pages (site
# navigation, "recommended jobs" spam, sign-in prompts) — text is cut off
# the moment any of these appears, since nothing useful follows them.
JUNK_MARKERS = [
    "referrals increase your chances", "get notified about new",
    "people also viewed", "similar jobs", "you may also like",
    "by clicking continue to join or sign in", "agree to linkedin's user agreement",
    "see this and similar jobs on linkedin", "get ai-powered advice on this job",
    "show more", "show less", "jobs recommended for you",
    "based on your profile", "other jobs at",
]
# If, after cutting junk, less than this many characters remain, the page
# was effectively a JS-rendered shell with no real content in the raw HTML.
MIN_REAL_CONTENT_LEN = 150


def strip_boilerplate(text: str) -> str:
    """Cut a scraped/pasted block off at the first sign of job-board
    navigation chrome or sign-in prompts, so section-splitting never runs
    into 'recommended jobs' spam or LinkedIn's consent boilerplate."""
    if not text:
        return text
    lower = text.lower()
    cut_at = len(text)
    for marker in JUNK_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and idx < cut_at:
            cut_at = idx
    return text[:cut_at].strip()


# ------------------------------------------------------------------ #
# Pasted-page cleaner. When someone Ctrl+A / Ctrl+C's a whole LinkedIn (or
# similar) page, the clipboard contains far more than the posting: the
# company blurb, a list of 25 "recommended jobs", button labels, footer
# links, and the description repeated once per "… more" expansion. This
# pulls out only the real posting and drops the rest.
# ------------------------------------------------------------------ #
_UI_NOISE_RE = re.compile(
    r"^(easy apply|apply|save|viewed|promoted|show (more|less|match details)|… ?more|\.\.\. ?more|"
    r"responses managed off linkedin|your profile and resume match.*|beta\b.*|"
    r"people you can reach out to|school alumni from .*|actively reviewing applicants|"
    r"over \d+ (people|applicants).*|\d+ (people|applicants) clicked apply|"
    r"unlock hiring insights.*|employee growth|applicant (education|seniority) level|"
    r"have a (master|bachelor).*|entry level applicants|1-month free trial.*|"
    r"\d+ school alumni works? here|\d+ connections? work here|verified job|"
    r"job description|requirements|about the job|about the company)$",
    re.I,
)
_STOP_MARKERS = ["unlock hiring insights", "about the company", "accessibility", "help center"]
_META_LINE_RE = re.compile(r"·\s*(reposted\s+)?\d+\s+\w+\s+ago\b", re.I)
_COMPANY_STAT_RE = re.compile(
    r"^(•|·|[\d,\.]+[kKmM]?\+?\s+(followers|on linkedin)|[\d,]+(-[\d,]+|\+)?\s+employees|\d+\s+on linkedin"
    r"|(…|\.\.\.)\s*more|see (more|less))$", re.I)
_MORE_TAIL_RE = re.compile(r"\s*(…|\.\.\.)\s*(more|see more)\s*$", re.I)


# Matches "… more", "...more", "…\nmore", "… see more", "see more", "show less" etc.,
# including when the ellipsis and the word are split across lines / nbsp.
_MORE_ANYWHERE_RE = re.compile(
    r"[ \t\u00a0]*(?:(?:…|\.{2,3})\s*(?:see\s+)?more\b|(?:^|(?<=\n))[ \t]*(?:see|show)\s+(?:more|less)[ \t]*$)",
    re.I | re.M,
)


def strip_more_markers(text: str) -> str:
    if not text:
        return text
    text = _MORE_ANYWHERE_RE.sub("", text)
    lines = [l for l in text.split("\n") if l.strip() not in ("…", "...", "more", "See more", "Show more", "Show less")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clean_company_profile(text: str) -> str:
    """Tidy a company blurb scraped from a page: drop follower/size/'on LinkedIn'
    stat lines, bullets, the industry tag under the follower count, '… more'
    markers, and a repeated 'Name — Name' header."""
    text = strip_more_markers(text or "")
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n")]
    # "Name — Name" duplicate header on the first line
    if lines and " — " in lines[0]:
        a, b = [x.strip() for x in lines[0].split(" — ", 1)]
        lines[0] = a if a.lower() == b.lower() else lines[0]
    out, after_followers = [], False
    for i, l in enumerate(lines):
        if not l:
            continue
        if re.search(r"^[\d,\.]+[kKmM]?\+?\s+followers$", l, re.I):
            after_followers = True
            continue
        if _COMPANY_STAT_RE.match(l) or re.match(r"^follow$", l, re.I):
            continue
        if after_followers:
            after_followers = False
            if len(l) < 60 and not l.endswith("."):   # industry tag line
                continue
        out.append(l)
    # If the header line is just the company name and the next line repeats it, drop the repeat
    if len(out) > 1 and out[0].split(" — ")[0].strip().lower() == out[1].strip().lower():
        out.pop(1)
    if len(out) > 1 and " — " not in out[0] and len(out[0]) < 80 and not out[0].endswith("."):
        out = [out[0] + " — " + out[1]] + out[2:]     # keep the "Company — blurb" shape
    return dedupe_lines("\n".join(out))


def sanitize_record(record: dict) -> dict:
    """Clean text fields of a job record in place (safe to call repeatedly)."""
    if not isinstance(record, dict):
        return record
    for k in ("description", "requirements", "benefits"):
        if record.get(k):
            record[k] = strip_more_markers(record[k])
    if record.get("company_profile"):
        record["company_profile"] = clean_company_profile(record["company_profile"])
    return record


def dedupe_lines(text: str, min_len: int = 25) -> str:
    """Drop repeated lines (pages often paste the same block 2-3 times).
    Short lines are kept as-is so bullets like 'Yes'/'No' aren't lost."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        key = re.sub(r"\s+", " ", line).strip().lower()
        if len(key) >= min_len:
            if key in seen:
                continue
            seen.add(key)
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def clean_pasted_page(text: str) -> dict:
    """Return {title, company, company_profile, body}. Falls back to just
    de-duplicating when the text doesn't look like a job-board page."""
    text = strip_more_markers(text or "")
    lines = [l.strip() for l in text.splitlines()]
    lower = [l.lower() for l in lines]
    out = {"title": "", "company": "", "company_profile": "", "body": ""}

    # 1) Locate the real posting: the last "About the job" heading marks it.
    start = next((i for i in range(len(lines) - 1, -1, -1) if lower[i] == "about the job"), None)
    if start is None:
        out["body"] = dedupe_lines(text)
        return out

    # 2) Title/company sit just above the "City · 6 months ago · N clicked apply" line.
    meta_idx = next((i for i in range(start - 1, max(-1, start - 40), -1) if _META_LINE_RE.search(lines[i])), None)
    if meta_idx is not None:
        above = [l for l in lines[max(0, meta_idx - 4):meta_idx] if l]
        if above:
            out["title"] = above[-1]
            if len(above) > 1:
                out["company"] = above[-2]

    # 3) Body = everything after "About the job" up to the first trailing marker.
    body_lines = []
    for i in range(start + 1, len(lines)):
        if any(lower[i].startswith(m) for m in _STOP_MARKERS):
            break
        body_lines.append(lines[i])
    body = "\n".join(_MORE_TAIL_RE.sub("", l) for l in body_lines if not _UI_NOISE_RE.match(l))
    body = dedupe_lines(body)
    # drop dangling section headings left at the very end (e.g. "Requirements:-")
    body_ls = body.splitlines()
    while body_ls and len(body_ls[-1].strip()) <= 40 and body_ls[-1].strip().endswith((":", ":-", "-")):
        body_ls.pop()
    out["body"] = "\n".join(body_ls).strip()

    # 4) Company blurb: the "About the company" section, minus follower/size stats.
    about = next((i for i in range(start, len(lines)) if lower[i] == "about the company"), None)
    if about is not None:
        prof, after_followers = [], False
        for l in lines[about + 1:]:
            ll = l.lower()
            if ll.startswith("unlock hiring insights") or ll.rstrip(":") in ("requirements", "job description", "about the job"):
                break
            if not l or l == out["company"] or _UI_NOISE_RE.match(l) or _COMPANY_STAT_RE.match(l):
                if re.search(r"followers$", ll):
                    after_followers = True
                continue
            if after_followers:            # the industry tag line right after "N followers"
                after_followers = False
                if len(l) < 60 and not l.endswith("."):
                    continue
            prof.append(_MORE_TAIL_RE.sub("", l))
        out["company_profile"] = dedupe_lines("\n".join(prof))
    return out


def _find_all_header_hits(text: str) -> list:
    """Find heading LINES (not stray mid-sentence matches) in document
    order, tagged with their category. A line counts as a heading only if
    it's short and starts with (or equals) a known header phrase — this is
    what stops 'the key requirements for this role include:' from being
    mistaken for a 'Requirements' heading just because the word appears in it."""
    categorized = (
        [(h, "requirements") for h in REQUIREMENT_HEADERS] +
        [(h, "benefits") for h in BENEFIT_HEADERS] +
        [(h, "company") for h in COMPANY_HEADERS] +
        [(h, "responsibilities") for h in RESPONSIBILITY_HEADERS]
    )
    # Longer phrases first, so "what you'll need" wins over a shorter
    # overlapping phrase on the same line.
    categorized.sort(key=lambda p: -len(p[0]))

    hits = []
    offset = 0
    for line in text.split("\n"):
        stripped = line.strip()
        heading_candidate = stripped.rstrip(":").strip().lower()
        if 0 < len(stripped) <= 60:
            for phrase, category in categorized:
                if heading_candidate == phrase or heading_candidate.startswith(phrase):
                    hits.append((offset, offset + len(line), category))
                    break
        offset += len(line) + 1  # +1 for the '\n' consumed by split
    return hits


def segment_description(text: str) -> dict:
    """Split a raw job-description blob (already boilerplate-stripped) into
    requirements / benefits / company / responsibilities buckets by walking
    every HEADING LINE in document order and assigning the text up to the
    next heading to the category that introduced it. Multiple synonym
    headings for the same category (e.g. 'Requirements' followed by
    'What You'll Bring') are merged rather than truncating each other."""
    if not text:
        return {}
    hits = _find_all_header_hits(text)
    if not hits:
        return {}

    buckets = {"requirements": [], "benefits": [], "company": [], "responsibilities": []}
    for i, (start, header_end, category) in enumerate(hits):
        content_end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        chunk = text[header_end:content_end].strip(" -:\n*•\t")
        if len(chunk) > 10:
            buckets[category].append(chunk)

    return {k: "\n".join(v) for k, v in buckets.items() if v}


def _detect_employment_type(text: str) -> str:
    t = text.lower()
    if re.search(r"\bfull[\s-]?time\b", t):
        return "Full-time"
    if re.search(r"\bpart[\s-]?time\b", t):
        return "Part-time"
    if re.search(r"\bcontract(or)?\b|\bfreelance\b|\bfixed[\s-]?term\b", t):
        return "Contract"
    if re.search(r"\btemporary\b|\btemp\b|\bseasonal\b", t):
        return "Temporary"
    return ""


def _detect_experience(text: str, title: str) -> str:
    t = (title + " " + text).lower()
    if re.search(r"\bintern(ship)?\b", t):
        return "Internship"
    if re.search(r"\b(chief|vp|vice president|c[eflotmx]o)\b", t):
        return "Executive"
    if re.search(r"\bdirector\b|\bhead of\b", t):
        return "Director"
    if re.search(r"\bsenior\b|\bsr\.?\b|\blead\b|\bprincipal\b|\bstaff\b", t):
        return "Mid-Senior level"
    if re.search(r"\bentry[\s-]?level\b|\bjunior\b|\bjr\.?\b|\bno experience\b|\bgraduate\b|\bfresher\b", t):
        return "Entry level"
    years = re.search(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?years?", t)
    if years:
        n = int(years.group(1))
        if n >= 8:
            return "Mid-Senior level"
        if n >= 3:
            return "Associate"
        return "Entry level"
    if re.search(r"\bmid[\s-]?level\b|\bassociate\b", t):
        return "Associate"
    return ""


def _detect_education(text: str) -> str:
    t = text.lower()
    if re.search(r"\bph\.?d\b|\bdoctorate\b", t):
        return "Doctorate"
    if re.search(r"\bmaster'?s\b|\bmba\b|\bm\.?s\.?c?\b", t):
        return "Master's Degree"
    if re.search(r"\bbachelor'?s\b|\bb\.?s\.?c?\b|\bundergraduate degree\b|\bb\.?a\.?\b", t):
        return "Bachelor's Degree"
    if re.search(r"\bassociate'?s? degree\b", t):
        return "Associate Degree"
    if re.search(r"\bcertification\b|\bcertificate\b", t):
        return "Certification"
    if re.search(r"\bhigh school\b", t):
        return "High School or equivalent"
    if re.search(r"\bvocational\b", t):
        return "Vocational"
    return ""


def _keyword_match(text: str, title: str, mapping: dict) -> str:
    t = (title + " " + text).lower()
    for label, keywords in mapping.items():
        for kw in keywords:
            if kw in t:
                return label
    return ""


def _detect_remote(text: str, title: str, location: str, job_location_type: str = "") -> bool:
    """Whether the role itself is remote. Deliberately conservative: a bare
    'remote' anywhere in a long description is a common false positive
    (e.g. a Program Manager posting on-site work that just happens to
    mention 'coordinating with remote stakeholders'). Short, curated
    fields (title/location) are trusted with the bare keyword; the body
    text needs an explicit "this role is remote"-style phrase."""
    if job_location_type and "telecommute" in job_location_type.lower():
        return True

    short_fields = (title + " " + location).lower()
    if re.search(r"\bremote\b|\bwork from home\b|\bwfh\b|\btelecommute\b", short_fields):
        return True

    t = text.lower()
    strong_phrases = [
        r"\bfully[\s-]?remote\b", r"\bremote[\s-]?first\b", r"100%\s*remote",
        r"\bwork from home\b", r"\bwfh\b", r"\btelecommute\b",
        r"\bremote position\b", r"\bremote role\b", r"\bremote job\b",
        r"\bremote opportunity\b", r"\bremote[\s-]?eligible\b",
        r"\bthis (?:is|role is|position is) remote\b", r"\bopen to remote\b",
    ]
    if any(re.search(p, t) for p in strong_phrases):
        return True
    # "Remote" as its own line (e.g. a "Location: Remote" style field
    # that lost its label during text extraction) is still a real signal.
    return any(line.strip().lower() == "remote" for line in text.split("\n"))


def derive_fields(job: dict) -> dict:
    """Fill in any missing structured fields on `job` using heuristics,
    and record which ones were auto-derived (vs directly scraped) so the
    UI can flag them for the user to double-check."""
    derived_flags = []

    # Cut off navigation/recommendation-spam/sign-in chrome before anything else.
    for _k in ("description", "requirements", "benefits", "company_profile"):
        if job.get(_k):
            job[_k] = strip_more_markers(job[_k])
    job["description"] = strip_boilerplate(job.get("description", ""))
    job["requirements"] = strip_boilerplate(job.get("requirements", ""))
    job["benefits"] = strip_boilerplate(job.get("benefits", ""))

    full_text = " ".join([
        job.get("description", ""), job.get("requirements", ""),
        job.get("benefits", ""), job.get("company_profile", ""),
    ])
    title = job.get("title", "")
    sections = segment_description(job.get("description", ""))

    if not job.get("requirements") and sections.get("requirements"):
        job["requirements"] = sections["requirements"][:2500]
        derived_flags.append("requirements")

    if not job.get("benefits") and sections.get("benefits"):
        job["benefits"] = sections["benefits"][:2500]
        derived_flags.append("benefits")

    # Prefer a real "About the company" paragraph over a bare org name —
    # a name alone isn't a profile worth re-deriving from.
    existing_profile = job.get("company_profile", "")
    company_section = sections.get("company", "")
    if company_section and len(company_section) > len(existing_profile):
        job["company_profile"] = (
            (existing_profile + " — " + company_section)
            if existing_profile and existing_profile not in company_section
            else company_section
        )[:2500]
        derived_flags.append("company_profile")

    if not job.get("employment_type") or job.get("employment_type") == "Unknown":
        val = _detect_employment_type(full_text)
        if val:
            job["employment_type"] = val
            derived_flags.append("employment_type")

    if not job.get("required_experience") or job.get("required_experience") == "Unknown":
        val = _detect_experience(full_text, title)
        if val:
            job["required_experience"] = val
            derived_flags.append("required_experience")

    if not job.get("required_education") or job.get("required_education") == "Unknown":
        val = _detect_education(full_text)
        if val:
            job["required_education"] = val
            derived_flags.append("required_education")

    if not job.get("industry") or job.get("industry") == "Unknown":
        val = _keyword_match(full_text, title, INDUSTRY_KEYWORDS)
        if val:
            job["industry"] = val
            derived_flags.append("industry")

    if not job.get("function") or job.get("function") == "Unknown":
        val = _keyword_match(full_text, title, FUNCTION_KEYWORDS)
        if val:
            job["function"] = val
            derived_flags.append("function")

    # Booleans
    job["telecommuting"] = 1 if _detect_remote(
        full_text, title, job.get("location", ""), job.get("job_location_type", "")
    ) else 0
    if job["telecommuting"]:
        derived_flags.append("telecommuting")

    if "has_company_logo" not in job or job.get("has_company_logo") is None:
        job["has_company_logo"] = 1 if job.get("logo_found") else 0
    if job.get("has_company_logo"):
        derived_flags.append("has_company_logo")

    # has_questions genuinely can't be observed from a listing page (it's a
    # behaviour of the *application form*, not the posting) — leave it off
    # and let the user set it manually.
    job.setdefault("has_questions", 0)

    job["derived_fields"] = derived_flags
    return job
