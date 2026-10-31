
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

REQUEST_TIMEOUT = 4  # seconds, per external call — keep the whole endpoint responsive
NEW_DOMAIN_THRESHOLD_DAYS = 180

FREE_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
    "aol.com", "icloud.com", "protonmail.com", "proton.me", "mail.com",
    "yandex.com", "zoho.com", "gmx.com", "rediffmail.com", "inbox.com",
}

JOB_BOARD_DOMAINS = {
    "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com", "monster.com",
    "ziprecruiter.com", "greenhouse.io", "lever.co", "workday.com", "myworkdayjobs.com",
    "smartrecruiters.com", "bamboohr.com", "icims.com", "jobvite.com", "dice.com",
    "simplyhired.com", "careerbuilder.com", "angel.co", "wellfound.com",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+")


def _root_domain(host: str) -> str:
    host = host.lower().strip().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_emails(text: str) -> list:
    if not text:
        return []
    seen, out = set(), []
    for m in EMAIL_RE.findall(text):
        e = m.lower()
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out[:5]


def extract_domains(text: str, source_url: str = "") -> list:
    """Pull candidate company-website domains out of free text + the
    posting's own source URL, skipping job-board platform domains."""
    domains = []
    seen = set()

    def add(host):
        d = _root_domain(host)
        if d and d not in seen and d not in JOB_BOARD_DOMAINS and "." in d:
            seen.add(d)
            domains.append(d)

    for m in URL_RE.findall(text or ""):
        try:
            add(urlparse(m).netloc)
        except ValueError:
            continue

    if source_url:
        try:
            add(urlparse(source_url).netloc)
        except ValueError:
            pass

    return domains[:5]


def check_mx(domain: str):
    """Returns (has_mx: bool | None, detail: str). None = couldn't verify."""
    try:
        import dns.resolver
    except ImportError:
        return None, "dnspython not installed — MX check skipped"
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = REQUEST_TIMEOUT
        resolver.lifetime = REQUEST_TIMEOUT
        answers = resolver.resolve(domain, "MX")
        return (len(answers) > 0), f"{len(answers)} mail server(s) found"
    except Exception as e:
        # NXDOMAIN, NoAnswer, Timeout, NoNameservers, etc. all collapse to
        # "no working mail server found", which is the actionable signal.
        return False, f"no MX record ({type(e).__name__})"


def check_reachable(domain: str):
    """Returns (reachable: bool | None, status: int | None)."""
    for scheme in ("https://", "http://"):
        try:
            resp = requests.head(scheme + domain, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            return True, resp.status_code
        except requests.exceptions.SSLError:
            continue
        except requests.RequestException:
            continue
    # HEAD can be blocked by some servers even when the site is fine; try GET once.
    try:
        resp = requests.get("https://" + domain, timeout=REQUEST_TIMEOUT)
        return True, resp.status_code
    except requests.RequestException:
        return False, None


def check_whois(domain: str):
    """Returns dict: {age_days, created, registrar, error}. Fields are
    None when unavailable rather than raising."""
    out = {"age_days": None, "created": None, "registrar": None, "error": None}
    try:
        import whois
    except ImportError:
        out["error"] = "python-whois not installed"
        return out
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(REQUEST_TIMEOUT)
        try:
            w = whois.whois(domain)
        finally:
            socket.setdefaulttimeout(old_timeout)

        created = w.creation_date
        if isinstance(created, list):
            created = created[0] if created else None
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - created).days
            out["age_days"] = age_days
            out["created"] = created.strftime("%Y-%m-%d")

        registrar = w.registrar
        if isinstance(registrar, list):
            registrar = registrar[0] if registrar else None
        out["registrar"] = registrar

        if out["age_days"] is None and out["registrar"] is None:
            out["error"] = "registry returned no usable WHOIS data for this domain"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def verify_email(email: str, company_domains: list) -> dict:
    domain = email.split("@")[-1].lower()
    is_free = domain in FREE_EMAIL_PROVIDERS
    has_mx, mx_detail = check_mx(domain)
    matches_company = (not company_domains) or any(domain == d for d in company_domains)

    flags = []
    if is_free:
        flags.append(f"'{email}' uses a free/personal email provider ({domain}), not a company domain")
    if has_mx is False:
        flags.append(f"'{email}' domain ({domain}) has no working mail server — likely cannot receive mail")
    if not is_free and company_domains and not matches_company:
        flags.append(f"'{email}' domain ({domain}) doesn't match the company website domain ({', '.join(company_domains)})")

    return {
        "email": email, "domain": domain, "is_free_provider": is_free,
        "has_mx": has_mx, "mx_detail": mx_detail,
        "matches_company_domain": matches_company if company_domains else None,
        "flags": flags,
    }


def verify_domain(domain: str) -> dict:
    reachable, status = check_reachable(domain)
    whois_info = check_whois(domain)

    flags = []
    if reachable is False:
        flags.append(f"{domain} did not respond to an HTTP request — no live website found")
    age_days = whois_info.get("age_days")
    if age_days is not None and age_days < NEW_DOMAIN_THRESHOLD_DAYS:
        flags.append(f"{domain} was registered only {age_days} day(s) ago (newly-registered domains are a classic scam signal)")

    return {
        "domain": domain, "reachable": reachable, "http_status": status,
        "whois": whois_info, "flags": flags,
    }


def run_verification(record: dict, source_url: str = "") -> dict:
    text = " ".join([
        record.get("company_profile", "") or "",
        record.get("description", "") or "",
        record.get("requirements", "") or "",
        record.get("benefits", "") or "",
    ])

    domains = extract_domains(text, source_url)
    emails = extract_emails(text)

    domain_checks = [verify_domain(d) for d in domains]
    email_checks = [verify_email(e, domains) for e in emails]

    all_flags = []
    for d in domain_checks:
        all_flags.extend(d["flags"])
    for e in email_checks:
        all_flags.extend(e["flags"])

    return {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "domains_found": domains,
        "emails_found": emails,
        "domain_checks": domain_checks,
        "email_checks": email_checks,
        "verification_flags": all_flags,
        "nothing_to_check": not domains and not emails,
    }
