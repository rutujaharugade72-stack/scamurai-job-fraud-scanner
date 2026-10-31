
import json
import re

import requests
from bs4 import BeautifulSoup

from derive import derive_fields, strip_boilerplate, clean_pasted_page, dedupe_lines
from security import safe_get, UnsafeURLError

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 12
MIN_USABLE_CONTENT_LEN = 120


class ScrapeError(Exception):
    pass


def _to_text(value) -> str:
    """Some sites (e.g. Apna) put a list or object where schema.org allows
    plain text (qualifications, experienceRequirements, description...).
    Flatten those into readable text so nothing downstream crashes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(t for t in (_to_text(v) for v in value) if t)
    if isinstance(value, dict):
        for key in ("description", "name", "value", "text"):
            if value.get(key):
                return _to_text(value[key])
        return "\n".join(
            t for k, v in value.items() if not str(k).startswith("@")
            for t in [_to_text(v)] if t
        )
    return str(value)


def _strip_html(html_text) -> str:
    html_text = _to_text(html_text)
    if not html_text:
        return ""
    # Preserve paragraph/list-item boundaries as newlines so downstream
    # heading-line detection (see derive.segment_description) can still
    # recognize "Requirements" etc. as their own line, not buried mid-sentence.
    return BeautifulSoup(html_text, "html.parser").get_text(separator="\n", strip=True)


# ------------------------------------------------------------------ #
# JSON-LD (schema.org JobPosting)
# ------------------------------------------------------------------ #
def _find_jobposting_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        expanded = []
        for c in candidates:
            if isinstance(c, dict) and "@graph" in c:
                expanded.extend(c["@graph"])
            else:
                expanded.append(c)
        for item in expanded:
            if isinstance(item, dict) and item.get("@type") in ("JobPosting", ["JobPosting"]):
                return item
    return None


EMPLOYMENT_TYPE_MAP = {
    "FULL_TIME": "Full-time", "FULLTIME": "Full-time", "FULL-TIME": "Full-time",
    "PART_TIME": "Part-time", "PARTTIME": "Part-time", "PART-TIME": "Part-time",
    "CONTRACTOR": "Contract", "CONTRACT": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Other", "INTERNSHIP": "Other",
    "VOLUNTEER": "Other", "PER_DIEM": "Other", "OTHER": "Other",
}


def _map_employment_type(raw) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    key = re.sub(r"[\s\-]+", "_", raw.strip().upper())
    return EMPLOYMENT_TYPE_MAP.get(key, raw.strip().title())


def _from_jsonld(job: dict, url: str) -> dict:
    org = job.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    loc = job.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    address = loc.get("address", {}) if isinstance(loc, dict) else {}
    location_str = ", ".join(
        filter(None, [
            address.get("addressLocality") if isinstance(address, dict) else None,
            address.get("addressRegion") if isinstance(address, dict) else None,
            address.get("addressCountry") if isinstance(address, dict) else None,
        ])
    )

    salary = ""
    base_salary = job.get("baseSalary")
    if isinstance(base_salary, dict):
        val = base_salary.get("value", {})
        if isinstance(val, dict):
            lo, hi, cur = val.get("minValue"), val.get("maxValue"), base_salary.get("currency", "")
            if lo or hi:
                salary = f"{cur} {lo or ''}-{hi or ''}".strip()

    employment_type = job.get("employmentType") or ""
    employment_type = _map_employment_type(employment_type)

    job_location_type = job.get("jobLocationType", "")
    if isinstance(job_location_type, list):
        job_location_type = job_location_type[0] if job_location_type else ""

    logo_found = bool(isinstance(org, dict) and org.get("logo"))

    return {
        "title": (job.get("title") or "").strip(),
        "company_profile": (org.get("name") or "") + (
            (" -- " + _strip_html(org.get("description", ""))) if isinstance(org, dict) and org.get("description") else ""
        ),
        "description": _strip_html(job.get("description", "")),
        "requirements": _strip_html(job.get("qualifications") or job.get("experienceRequirements") or ""),
        "benefits": _strip_html(job.get("jobBenefits", "")),
        "employment_type": employment_type,
        "industry": job.get("industry", "") if isinstance(job.get("industry"), str) else "",
        "function": job.get("occupationalCategory", "") if isinstance(job.get("occupationalCategory"), str) else "",
        "location": location_str,
        "salary_range": salary,
        "job_location_type": job_location_type,
        "logo_found": logo_found,
        "source_url": url,
        "extraction_method": "structured data (JSON-LD)",
    }


# ------------------------------------------------------------------ #
# LinkedIn guest-page specific parsing
# ------------------------------------------------------------------ #
def _is_linkedin(url: str) -> bool:
    return "linkedin.com" in url.lower()


def _linkedin_criteria(soup: BeautifulSoup) -> dict:
    """LinkedIn's public job page shows a 'job criteria' list with
    Seniority level / Employment type / Job function / Industries.
    Pull those directly -- they're LinkedIn's own categorization, not a guess."""
    out = {}
    items = soup.select("li.description__job-criteria-item") or soup.select("li[class*='job-criteria-item']")
    for li in items:
        header = li.find(["h3", "h4"])
        value = li.find("span")
        if not header or not value:
            continue
        label = header.get_text(strip=True).lower()
        text = value.get_text(strip=True)
        if "seniority" in label:
            out["required_experience"] = text
        elif "employment type" in label:
            out["employment_type"] = text
        elif "job function" in label:
            out["function"] = text
        elif "industries" in label or "industry" in label:
            out["industry"] = text
    return out


def _linkedin_enrich(soup: BeautifulSoup, job: dict) -> dict:
    if not job.get("title"):
        h1 = soup.select_one("h1.top-card-layout__title, h1.topcard__title, h1")
        if h1:
            job["title"] = h1.get_text(strip=True)

    if not job.get("company_profile"):
        org = soup.select_one("a.topcard__org-name-link, span.topcard__flavor, a[class*='org-name']")
        if org:
            job["company_profile"] = org.get_text(strip=True)

    if not job.get("description") or len(job.get("description", "")) < MIN_USABLE_CONTENT_LEN:
        desc = soup.select_one(
            "div.description__text, div.show-more-less-html__markup, div[class*='description__text']"
        )
        if desc:
            job["description"] = desc.get_text(separator=" ", strip=True)

    if not job.get("location"):
        loc = soup.select_one("span.topcard__flavor--bullet, span[class*='bullet']")
        if loc:
            job["location"] = loc.get_text(strip=True)

    if not job.get("logo_found"):
        img = soup.select_one("img.artdeco-entity-image, img[class*='company-logo']")
        job["logo_found"] = bool(img and img.get("src"))

    criteria = _linkedin_criteria(soup)
    for key, val in criteria.items():
        if not job.get(key):
            job[key] = val

    return job


# ------------------------------------------------------------------ #
# Generic fallback for any other site
# ------------------------------------------------------------------ #
def _generic_fallback(soup: BeautifulSoup, url: str) -> dict:
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    title = re.sub(r"\s*[\|\-\u2013]\s*(LinkedIn|Indeed|Naukri).*$", "", title, flags=re.I)

    meta_desc = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        meta_desc = meta["content"]

    body_text = ""
    main = soup.find("main") or soup.find("article") or soup.body
    if main:
        paragraphs = [p.get_text(" ", strip=True) for p in main.find_all(["p", "li", "h1", "h2", "h3", "h4"])]
        body_text = "\n".join(p for p in paragraphs if len(p) > 5)[:6000]

    og_image = soup.find("meta", attrs={"property": "og:image"})
    logo_found = bool(og_image and og_image.get("content"))

    return {
        "title": title,
        "company_profile": "",
        "description": (meta_desc + " " + body_text).strip(),
        "requirements": "",
        "benefits": "",
        "employment_type": "",
        "industry": "",
        "function": "",
        "location": "",
        "salary_range": "",
        "logo_found": logo_found,
        "source_url": url,
        "extraction_method": "generic page scan (please review the fields below)",
    }


# ------------------------------------------------------------------ #
# Main entry point
# ------------------------------------------------------------------ #
def parse_pasted_text(text: str) -> dict:
    """Turn raw text the user copy-pasted straight off a job page into a
    structured job dict, using the same section-detection/derivation logic
    as URL imports. This is the reliable path for JS-heavy sites (LinkedIn,
    Indeed, etc.) where a direct HTTP fetch can't see the rendered page —
    the user's browser already has it, so we just need the text."""
    text = (text or "").strip()
    if len(text) < MIN_USABLE_CONTENT_LEN:
        raise ScrapeError(
            "Please paste more of the job posting (title, description, requirements, etc.) "
            "— that looked too short to work with."
        )

    cleaned = clean_pasted_page(text)
    if cleaned["title"]:
        # Recognised a full job-board page: title/company/body already isolated.
        title = cleaned["title"]
        remaining = strip_boilerplate(cleaned["body"])
    else:
        text = strip_boilerplate(cleaned["body"])
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        title = ""
        remaining = text
        if lines and len(lines[0]) < 120 and not lines[0].endswith((".", ",")):
            title = lines[0]
            remaining = "\n".join(lines[1:]).strip()

    job = {
        "title": title,
        "company_profile": " — ".join(x for x in (cleaned["company"], cleaned["company_profile"]) if x),
        "description": remaining,
        "requirements": "",
        "benefits": "",
        "employment_type": "",
        "required_experience": "",
        "required_education": "",
        "industry": "",
        "function": "",
        "location": "",
        "salary_range": "",
        "logo_found": False,
        "source_url": "",
        "extraction_method": "pasted text (auto-segmented)",
    }
    job = derive_fields(job)

    # Requirements/benefits were carved out of the description — don't show
    # the same lines twice in the report.
    moved = {re.sub(r"\s+", " ", l).strip().lower()
             for k in ("requirements", "benefits") for l in job.get(k, "").splitlines() if len(l.strip()) > 8} | {"benefits", "benefits:", "requirements", "requirements:"}
    if moved:
        kept = [l for l in job["description"].splitlines()
                if re.sub(r"\s+", " ", l).strip().lower() not in moved]
        while kept and (not kept[-1].strip() or (len(kept[-1].strip()) <= 40 and kept[-1].strip().endswith((":", ":-", "-")))):
            kept.pop()
        job["description"] = dedupe_lines("\n".join(kept))
    return job


# ------------------------------------------------------------------ #
# Entry point used by the browser extension (src/extension) — the
# content script running inside the user's own authenticated tab reads
# the page's JobPosting JSON-LD directly (no HTTP fetch, no bot-block,
# since it's the real rendered DOM) and posts just that JSON-LD object
# here. Reuses the exact same parsing + derivation pipeline as a normal
# URL import so the two paths never drift apart.
# ------------------------------------------------------------------ #
def build_job_from_jsonld(job: dict, url: str = "") -> dict:
    parsed = _from_jsonld(job, url)
    parsed["extraction_method"] = "browser extension (page JSON-LD)"
    return derive_fields(parsed)


def fetch_job_from_url(url: str) -> dict:
    if not re.match(r"^https?://", url):
        url = "https://" + url

    try:
        resp = safe_get(url, headers=HEADERS, timeout=TIMEOUT)
    except UnsafeURLError as e:
        raise ScrapeError(f"That link can't be imported: {e}")
    except requests.RequestException as e:
        raise ScrapeError(f"Could not reach that URL ({e.__class__.__name__}). Check the link and try again.")

    if resp.status_code in (401, 403, 999):
        raise ScrapeError(
            "This site blocked automated access (common for LinkedIn/Indeed when logged out). "
            "Please copy-paste the job details manually instead."
        )
    if resp.status_code >= 400:
        raise ScrapeError(f"The page returned an error (HTTP {resp.status_code}). Check the link.")

    soup = BeautifulSoup(resp.text, "html.parser")
    is_linkedin = _is_linkedin(url)

    job = _find_jobposting_jsonld(soup)
    if job:
        job = _from_jsonld(job, url)
    else:
        job = _generic_fallback(soup, url)

    if is_linkedin:
        job = _linkedin_enrich(soup, job)
        if len(job.get("description", "")) >= MIN_USABLE_CONTENT_LEN:
            job["extraction_method"] = "LinkedIn page data"

    total_content = len(strip_boilerplate(job.get("description", ""))) + len(job.get("title", ""))
    if total_content < MIN_USABLE_CONTENT_LEN:
        hint = (
            " LinkedIn renders full job details with JavaScript after you're signed in, so a direct "
            "fetch only sees a short placeholder page."
            if is_linkedin else
            " The site may require login, or render its content with JavaScript that a direct fetch can't see."
        )
        raise ScrapeError(
            "Couldn't extract enough real job detail from this page." + hint +
            " Use \u2018Paste posting text\u2019 below instead \u2014 copy the job details from the page "
            "you're viewing and we'll auto-fill everything from that."
        )

    job = derive_fields(job)
    return job