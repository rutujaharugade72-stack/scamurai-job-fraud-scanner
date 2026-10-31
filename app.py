
import csv
import io
import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO

import joblib
import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory, send_file, redirect, url_for
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from preprocessing import record_to_frame, build_feature_vector, CATEGORICAL_FIELDS  # noqa: E402
from derive import sanitize_record  # noqa: E402
from scraper import fetch_job_from_url, parse_pasted_text, build_job_from_jsonld, ScrapeError  # noqa: E402
from report_pdf import build_report_pdf  # noqa: E402
import explain as explain_engine  # noqa: E402
import verify as verify_engine  # noqa: E402
from models import db, User, Scan, Feedback  # noqa: E402
from security import safe_next_url, rate_limit  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "models")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")

# .env is read BEFORE any setting is used (the original read SECRET_KEY first,
# so a key stored in .env was silently ignored).
_env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# SENTINEL_ENV: "development" (default, local use), "production", or "testing".
APP_ENV = os.environ.get("SENTINEL_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

_secret = os.environ.get("SENTINEL_SECRET_KEY", "").strip()
if not _secret:
    # Never ship a hard-coded key. A random per-process key is safe; the only
    # cost is that logins are reset on restart. Set SENTINEL_SECRET_KEY to keep them.
    _secret = secrets.token_hex(32)
    print("WARNING: SENTINEL_SECRET_KEY is not set - using a temporary random key "
          "(sessions will reset when the app restarts).")
app.config["SECRET_KEY"] = _secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION and os.environ.get("SENTINEL_COOKIE_SECURE", "1") == "1",
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=IS_PRODUCTION and os.environ.get("SENTINEL_COOKIE_SECURE", "1") == "1",
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,           # reject oversized request bodies
)
RATE_LIMITS_ON = APP_ENV != "testing" or os.environ.get("SENTINEL_FORCE_LIMITS") == "1"
BATCH_MAX_ROWS = int(os.environ.get("SENTINEL_BATCH_MAX_ROWS", "200"))


def _client_ip():
    return request.remote_addr or "unknown"


def _user_key():
    return current_user.get_id() if current_user.is_authenticated else _client_ip()


def _limits_enabled():
    return RATE_LIMITS_ON


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url.startswith("postgres://"):           
    _db_url = "postgresql://" + _db_url[len("postgres://"):]
if _db_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = _db_url                     # shared online DB (Postgres)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}
    print("Database: online (DATABASE_URL)")
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(INSTANCE_DIR, "sentinel.db")
    print("Database: local SQLite (instance/sentinel.db)")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.request_loader
def load_user_from_token(req):
    
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):].strip()
    if not token:
        return None
    return User.query.filter_by(api_token=token).first()


with app.app_context():
    db.create_all()
    
    try:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN api_token VARCHAR(64)"))
        db.session.commit()
        print("Migrated existing database: added users.api_token")
    except Exception:
        db.session.rollback()

    
    if not IS_PRODUCTION and not User.query.filter_by(username="demo").first():
        demo = User(username="demo")
        demo.set_password("demo1234")
        db.session.add(demo)
        db.session.commit()
        print("Seeded demo account -> username: demo / password: demo1234")


print("Loading model artifacts...")
model = joblib.load(os.path.join(MODELS_DIR, "model.pkl"))
vectorizer = joblib.load(os.path.join(MODELS_DIR, "tfidf.pkl"))
encoder = joblib.load(os.path.join(MODELS_DIR, "encoder.pkl"))
lr_explainer = joblib.load(os.path.join(MODELS_DIR, "lr_explainer.pkl"))
with open(os.path.join(MODELS_DIR, "model_info.json")) as f:
    MODEL_INFO = json.load(f)
# Threshold on the fraud probability (0-1). Written by `python src/evaluate_cv.py --apply`;
# SENTINEL_DECISION_THRESHOLD overrides it without retraining (lower = catch more scams,
# more false alarms). Unset and absent -> the model's own 0.5 rule.
DECISION_THRESHOLD = MODEL_INFO.get("decision_threshold")
_thr_env = os.environ.get("SENTINEL_DECISION_THRESHOLD", "").strip()
if _thr_env:
    try:
        _t = float(_thr_env)
        if 0.01 <= _t <= 0.99:
            DECISION_THRESHOLD = _t
            MODEL_INFO["decision_threshold"] = _t
        else:
            print("Ignoring SENTINEL_DECISION_THRESHOLD: must be between 0.01 and 0.99.")
    except ValueError:
        print("Ignoring SENTINEL_DECISION_THRESHOLD: not a number.")
print(f"Model loaded: {MODEL_INFO['best_model']}"
      + (f" (decision threshold {DECISION_THRESHOLD:.2f})" if DECISION_THRESHOLD else ""))
try:
    import shap  # noqa: F401
    print("SHAP available — explanations will use the Partition/Text explainer.")
except ImportError:
    print("SHAP not installed — explanations will fall back to the LR-coefficient approximation. "
          "Run `pip install shap` to enable real Shapley-value explanations.")


@app.route("/api/health")
def health():
    """Liveness probe for Docker / load balancers. No auth, no secrets."""
    return jsonify({"status": "ok", "model": MODEL_INFO.get("best_model"), "env": APP_ENV})


@app.route("/")
@login_required
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
@rate_limit(10, 60, _client_ip, "login", enabled=_limits_enabled)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(safe_next_url(request.args.get("next"), default=url_for("home")))
        error = "Incorrect username or password."
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
@rate_limit(6, 600, _client_ip, "register", enabled=_limits_enabled)
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords don't match."
        elif User.query.filter_by(username=username).first():
            error = "That username is already taken."
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(url_for("home"))
    return render_template("register.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/api/account/whoami")
@login_required
def whoami():
   
    return jsonify({"username": current_user.username})


@app.route("/api/account/token", methods=["GET", "POST"])
@login_required
def account_token():
   
    if request.method == "POST":
        current_user.api_token = None
    current_user.ensure_api_token()
    db.session.commit()
    return jsonify({"api_token": current_user.api_token})


@app.route("/api/extension/import-jsonld", methods=["POST"])
@login_required
def extension_import_jsonld():
    
    body = request.get_json(force=True) or {}
    jsonld = body.get("jsonld")
    url = (body.get("url") or "").strip()
    if not isinstance(jsonld, dict):
        return jsonify({"success": False, "error": "No JobPosting structured data was provided."}), 400
    try:
        job = build_job_from_jsonld(jsonld, url)
        return jsonify({"success": True, "job": job})
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not parse that posting: {e}"}), 500


@app.route("/api/model-info")
def model_info():
    
    feedback_total = Feedback.query.count()
    feedback_correct = Feedback.query.filter_by(is_correct=True).count()

    info = dict(MODEL_INFO)
    info["feedback_total"] = feedback_total
    info["feedback_correct"] = feedback_correct
    info["feedback_incorrect"] = feedback_total - feedback_correct
    return jsonify(info)

@app.route("/api/import-url", methods=["POST"])
@login_required
@rate_limit(20, 60, _user_key, "import-url", enabled=_limits_enabled)
def import_url():
    data = request.get_json(force=True)
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Please provide a URL."}), 400
    try:
        result = fetch_job_from_url(url)
        return jsonify({"success": True, "job": result})
    except ScrapeError as e:
        return jsonify({"success": False, "error": str(e)}), 422
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {e}"}), 500


@app.route("/api/parse-text", methods=["POST"])
@login_required
def parse_text():
    data = request.get_json(force=True)
    text = (data or {}).get("text", "")
    try:
        result = parse_pasted_text(text)
        return jsonify({"success": True, "job": result})
    except ScrapeError as e:
        return jsonify({"success": False, "error": str(e)}), 422
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {e}"}), 500


# ---- Rule-based scam checks (shown next to the ML verdict as risk signals) ----
_RED_FLAG_RULES = [
    ("Asks for a fee or deposit before hiring",
     r"\b(registration|security|training|joining|processing|application|kit|laptop|uniform|id card)\s+(fee|fees|charge|charges|deposit)\b"
     r"|\brefundable\s+(deposit|fee|amount)\b|\bpay\s+(a\s+)?(small\s+)?(fee|deposit)\b"),
    ("Asks candidates to contact via WhatsApp / Telegram",
     r"\b(whatsapp|telegram)\b"),
    ("Promises guaranteed income or a job",
     r"\bguaranteed\s+(income|salary|job|placement|earnings|returns)\b"),
    ("Unrealistic earnings claim (per day / per week)",
     r"\bearn\w*\s+(up\s+to\s+)?(₹|rs\.?|inr|\$)\s?[\d,]{5,}\s*(per|/|a)\s*(day|week)\b"),
    ("Says no interview is needed",
     r"\bno\s+interview\b|\bselected\s+without\s+(an\s+)?interview\b|\bdirect\s+joining\s+without\s+interview\b"),
    ("Asks to share OTP, PIN or bank details",
     r"\b(send|share|provide|give)\b.{0,40}\b(otp|cvv|atm\s+pin|bank\s+(account\s+)?(details|password)|password)\b"),
]


def red_flag_rules(record):
    text = " ".join(
        str(record.get(k) or "")
        for k in ("title", "company_profile", "description", "requirements", "benefits")
    ).lower()
    return [label for label, pattern in _RED_FLAG_RULES if re.search(pattern, text)]


TEXT_FIELDS = ["title", "company_profile", "description", "requirements", "benefits"]


def has_text_content(record):
    return any((record.get(f) or "").strip() for f in TEXT_FIELDS)


def risk_level(fraud_probability):
    """Human-friendly band used by the batch scanner (probability in %)."""
    if fraud_probability >= 70:
        return "High"
    if fraud_probability >= 40:
        return "Medium"
    return "Low"


def score_record(record, explain=True, top_k=8):
    """Run the full model on one (already sanitized) record and return the
    same dict /api/predict has always returned. explain=False skips the slow
    SHAP step (used by the batch scanner) and returns empty word lists."""
    df = record_to_frame(record)
    X = build_feature_vector(df, vectorizer, encoder)

    proba = model.predict_proba(X)[0]
    fraud_probability = float(proba[1]) * 100
    if DECISION_THRESHOLD:
        prediction = int(proba[1] >= DECISION_THRESHOLD)
    else:
        prediction = int(model.predict(X)[0])
    confidence = (fraud_probability if prediction == 1 else 100 - fraud_probability)
    if not DECISION_THRESHOLD:
        confidence = float(max(proba)) * 100          # unchanged legacy behaviour

    if explain:
        explanation = explain_engine.explain(df, vectorizer, encoder, model, lr_explainer, top_k=top_k)
    else:
        explanation = {"fraud_words": [], "legit_words": [], "method": "skipped (batch mode)",
                       "structured_impact": []}

    risk_factors = []
    if not record.get("has_company_logo"):
        risk_factors.append("No company logo provided")
    if not (record.get("company_profile") or "").strip():
        risk_factors.append("Missing company profile / description")
    if record.get("telecommuting"):
        risk_factors.append("Listed as fully remote / telecommuting")
    salary = (record.get("salary_range") or "").lower()
    if "unrealistic" in salary or "$$" in salary:
        risk_factors.append("Suspicious salary phrasing")
    if len((record.get("description") or "")) < 80:
        risk_factors.append("Very short job description")
    for flag in red_flag_rules(record):
        if flag not in risk_factors:
            risk_factors.append(flag)

    positive_factors = []
    if record.get("has_company_logo"):
        positive_factors.append("Company logo displayed")
    if (record.get("company_profile") or "").strip():
        positive_factors.append("Company profile / description provided")
    if (record.get("requirements") or "").strip():
        positive_factors.append("Clear requirements listed")
    if (record.get("benefits") or "").strip():
        positive_factors.append("Benefits information included")
    if (record.get("salary_range") or "").strip() and "unrealistic" not in salary and "$$" not in salary:
        positive_factors.append("Salary range clearly stated")
    if len((record.get("description") or "")) >= 80:
        positive_factors.append("Detailed job description provided")
    if (record.get("employment_type") or "Unknown") not in ("", "Unknown"):
        positive_factors.append("Employment type clearly specified")

    return {
        "prediction": prediction,
        "label": "FRAUDULENT" if prediction == 1 else "LEGITIMATE",
        "confidence": round(confidence, 1),
        "fraud_probability": round(fraud_probability, 1),
        "legit_probability": round(100 - fraud_probability, 1),
        "model_used": MODEL_INFO["best_model"],
        "fraud_words": explanation["fraud_words"],
        "legit_words": explanation["legit_words"],
        "fraud_word_scores": explanation.get("fraud_word_scores"),
        "legit_word_scores": explanation.get("legit_word_scores"),
        "explanation_method": explanation["method"],
        "explanation_base_value": explanation.get("base_value"),
        "explanation_truncated": explanation.get("truncated", False),
        "structured_impact": explanation.get("structured_impact", []),
        "risk_factors": risk_factors,
        "positive_factors": positive_factors,
    }


@app.route("/api/predict", methods=["POST"])
@login_required
@rate_limit(60, 60, _user_key, "predict", enabled=_limits_enabled)
def predict():
    record = sanitize_record(request.get_json(force=True) or {})
    if not has_text_content(record):
        return jsonify({"error": "Please provide at least a job title or description."}), 400
    return jsonify(score_record(record, explain=True))


# ---- Batch scanning: upload a CSV of postings, get a verdict for each row ----
_BATCH_ALIASES = {
    "title": ["title", "job_title", "job title", "position", "role"],
    "company_profile": ["company_profile", "company", "company_description", "about_company"],
    "description": ["description", "job_description", "details", "text"],
    "requirements": ["requirements", "qualifications", "skills"],
    "benefits": ["benefits", "perks"],
    "salary_range": ["salary_range", "salary", "pay", "compensation"],
    "employment_type": ["employment_type", "type", "job_type"],
    "telecommuting": ["telecommuting", "remote"],
    "has_company_logo": ["has_company_logo", "logo"],
    "has_questions": ["has_questions", "screening_questions"],
}
_TRUTHY = {"1", "true", "yes", "y", "t"}


def _normalise_headers(fieldnames):
    """Map the CSV's own column names onto Sentinel's field names."""
    lookup = {}
    for canonical, names in _BATCH_ALIASES.items():
        for n in names:
            lookup[n] = canonical
    mapping = {}
    for raw in fieldnames or []:
        key = (raw or "").strip().lower().lstrip("\ufeff")
        if key in lookup and lookup[key] not in mapping.values():
            mapping[raw] = lookup[key]
    return mapping


def parse_batch_csv(raw_bytes):
    """Return (records, error). Records are plain dicts ready for sanitize_record."""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    mapping = _normalise_headers(reader.fieldnames)
    if not any(v in ("title", "description") for v in mapping.values()):
        return None, ("The CSV needs a header row with at least a 'title' or 'description' column. "
                      "Optional columns: company_profile, requirements, benefits, salary_range, "
                      "employment_type, telecommuting, has_company_logo.")
    records = []
    for row in reader:
        rec = {}
        for raw, canonical in mapping.items():
            val = (row.get(raw) or "").strip()
            if canonical in ("telecommuting", "has_company_logo", "has_questions"):
                rec[canonical] = 1 if val.lower() in _TRUTHY else 0
            else:
                rec[canonical] = val
        records.append(rec)
        if len(records) > BATCH_MAX_ROWS:
            return None, f"Too many rows. The limit is {BATCH_MAX_ROWS} postings per upload."
    if not records:
        return None, "The CSV has a header but no data rows."
    return records, None


@app.route("/api/batch-scan", methods=["POST"])
@login_required
@rate_limit(6, 60, _user_key, "batch", enabled=_limits_enabled)
def batch_scan():
    """Multipart upload: field `file` (CSV). Optional form field save=1 stores
    every scored row in the user's History."""
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose a CSV file to upload."}), 400
    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"error": "Only .csv files are supported."}), 400
    records, err = parse_batch_csv(upload.read())
    if err:
        return jsonify({"error": err}), 400

    save = (request.form.get("save") or "").lower() in _TRUTHY
    rows, skipped, saved = [], 0, 0
    for i, raw in enumerate(records, start=1):
        record = sanitize_record(raw)
        if not has_text_content(record):
            skipped += 1
            rows.append({"row": i, "title": "", "label": "SKIPPED", "fraud_probability": None,
                         "risk_level": "n/a", "red_flags": [], "note": "Row has no text to analyse."})
            continue
        result = score_record(record, explain=False)
        flags = red_flag_rules(record)
        rows.append({
            "row": i,
            "title": (record.get("title") or "(untitled posting)")[:200],
            "label": result["label"],
            "fraud_probability": result["fraud_probability"],
            "risk_level": risk_level(result["fraud_probability"]),
            "red_flags": flags,
            "note": "",
        })
        if save:
            db.session.add(Scan(
                user_id=current_user.id,
                title=(record.get("title") or "(untitled posting)")[:300],
                label=result["label"], confidence=float(result["confidence"]),
                record=record, result=result,
                meta={"extraction_method": "batch CSV upload", "source_file": upload.filename[:120]},
            ))
            saved += 1
    if save:
        db.session.commit()

    scored = [r for r in rows if r["label"] != "SKIPPED"]
    summary = {
        "total_rows": len(rows),
        "scored": len(scored),
        "skipped": skipped,
        "fraudulent": sum(1 for r in scored if r["label"] == "FRAUDULENT"),
        "high_risk": sum(1 for r in scored if r["risk_level"] == "High"),
        "with_red_flags": sum(1 for r in scored if r["red_flags"]),
        "saved_to_history": saved,
    }
    return jsonify({"success": True, "summary": summary, "rows": rows})


@app.route("/api/report/pdf", methods=["POST"])
@login_required
def report_pdf():
    """Build the same detailed report shown in the Analyze/History views as
    a downloadable, print-ready PDF. Body: {record, result, meta}. `record`
    is the job-posting fields the form submitted, `result` is exactly what
    /api/predict returned for it, `meta` is optional (report_id,
    generated_at, source_url, extraction_method)."""
    body = request.get_json(force=True) or {}
    record = sanitize_record(body.get("record") or {})
    result = body.get("result") or {}
    meta = body.get("meta") or {}
    verification = body.get("verification") or None

    if not result or "prediction" not in result:
        return jsonify({"error": "Missing scan result — run a scan before downloading a report."}), 400

    meta.setdefault("generated_at", datetime.now().strftime("%d %b %Y, %I:%M %p"))

    try:
        pdf_bytes = build_report_pdf(record, result, meta, verification)
    except Exception as e:
        return jsonify({"error": f"Could not build the PDF report: {e}"}), 500

    slug = re.sub(r"[^a-z0-9]+", "-", (record.get("title") or "job-posting").lower()).strip("-")[:60] or "job-posting"
    filename = f"sentinel-report-{slug}.pdf"

    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/verify", methods=["POST"])
@login_required
def verify():
    
    body = request.get_json(force=True) or {}
    record = body.get("record") or {}
    source_url = (body.get("source_url") or record.get("source_url") or "").strip()
    try:
        result = verify_engine.run_verification(record, source_url)
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": f"Verification failed: {e}"}), 500


@app.route("/api/dashboard-stats", methods=["GET"])
@login_required
def dashboard_stats():
    
    from collections import Counter, defaultdict

    scans = current_user.scans.order_by(Scan.created_at.asc()).limit(500).all()

    total = len(scans)
    fraud_count = sum(1 for s in scans if s.label == "FRAUDULENT")
    legit_count = total - fraud_count
    avg_confidence = round(sum(s.confidence or 0 for s in scans) / total, 1) if total else 0

    # Daily trend, last 14 calendar days (UTC), zero-filled so the chart
    # always has a continuous x-axis even on days with no scans.
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - timedelta(days=i)) for i in range(13, -1, -1)]
    by_day = {d: {"total": 0, "fraud": 0} for d in day_keys}
    for s in scans:
        d = s.created_at.date()
        if d in by_day:
            by_day[d]["total"] += 1
            if s.label == "FRAUDULENT":
                by_day[d]["fraud"] += 1
    trend = [
        {"date": d.strftime("%d %b"), "total": by_day[d]["total"], "fraud": by_day[d]["fraud"]}
        for d in day_keys
    ]

    risk_counter = Counter()
    fraud_word_counter = Counter()
    employment_breakdown = defaultdict(lambda: {"total": 0, "fraud": 0})

    for s in scans:
        for rf in (s.result or {}).get("risk_factors") or []:
            risk_counter[rf] += 1
        for w in (s.result or {}).get("fraud_words") or []:
            fraud_word_counter[w.lower()] += 1
        et = (s.record or {}).get("employment_type") or "Unknown"
        employment_breakdown[et]["total"] += 1
        if s.label == "FRAUDULENT":
            employment_breakdown[et]["fraud"] += 1

    verification_checked = sum(1 for s in scans if s.verification)
    verification_flagged = sum(
        1 for s in scans if s.verification and (s.verification or {}).get("verification_flags")
    )

    return jsonify({
        "total_scans": total,
        "fraud_count": fraud_count,
        "legit_count": legit_count,
        "fraud_rate": round((fraud_count / total) * 100, 1) if total else 0,
        "avg_confidence": avg_confidence,
        "trend": trend,
        "top_risk_factors": [{"label": k, "count": v} for k, v in risk_counter.most_common(8)],
        "top_fraud_words": [{"word": k, "count": v} for k, v in fraud_word_counter.most_common(12)],
        "employment_breakdown": [
            {"label": k, "total": v["total"], "fraud": v["fraud"]}
            for k, v in sorted(employment_breakdown.items(), key=lambda kv: -kv[1]["total"])
        ],
        "verification_checked": verification_checked,
        "verification_flagged": verification_flagged,
    })


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(ASSETS_DIR, filename)



@app.route("/api/scans", methods=["POST"])
@login_required
def create_scan():
    body = request.get_json(force=True) or {}
    record = sanitize_record(body.get("record") or {})
    result = body.get("result") or {}
    meta = body.get("meta") or None
    if not result or "prediction" not in result:
        return jsonify({"error": "Missing scan result."}), 400

    scan = Scan(
        user_id=current_user.id,
        title=(record.get("title") or "(untitled posting)")[:300],
        label=result.get("label", ""),
        confidence=float(result.get("confidence", 0)),
        record=record, result=result, meta=meta, verification=None,
    )
    db.session.add(scan)
    db.session.commit()
    return jsonify({"success": True, "id": scan.id})


@app.route("/api/scans", methods=["GET"])
@login_required
def list_scans():
    scans = current_user.scans.order_by(Scan.created_at.desc()).limit(200).all()
    return jsonify({"scans": [s.to_summary() for s in scans]})


@app.route("/api/scans/<int:scan_id>", methods=["GET"])
@login_required
def get_scan(scan_id):
    scan = current_user.scans.filter_by(id=scan_id).first()
    if not scan:
        return jsonify({"error": "Scan not found."}), 404
    full = scan.to_full()
    full["record"] = sanitize_record(dict(full.get("record") or {}))   # also tidies older saved scans
    full["feedback"] = scan.feedback.is_correct if scan.feedback else None   # True / False / None (not answered yet)
    return jsonify(full)


@app.route("/api/scans/<int:scan_id>", methods=["PATCH"])
@login_required
def update_scan(scan_id):
    """Used to attach the external-verification result once it finishes —
    verification runs asynchronously after the scan is already saved."""
    scan = current_user.scans.filter_by(id=scan_id).first()
    if not scan:
        return jsonify({"error": "Scan not found."}), 404
    body = request.get_json(force=True) or {}
    if "verification" in body:
        scan.verification = body["verification"]
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/scans/<int:scan_id>", methods=["DELETE"])
@login_required
def delete_scan(scan_id):
    scan = current_user.scans.filter_by(id=scan_id).first()
    if not scan:
        return jsonify({"error": "Scan not found."}), 404
    db.session.delete(scan)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/scans", methods=["DELETE"])
@login_required
def clear_scans():
    current_user.scans.delete()
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/scans/<int:scan_id>/feedback", methods=["POST"])
@login_required
def save_feedback(scan_id):
   
    scan = current_user.scans.filter_by(id=scan_id).first()
    if not scan:
        return jsonify({"error": "Scan not found."}), 404
    body = request.get_json(force=True) or {}
    is_correct = body.get("is_correct")
    if not isinstance(is_correct, bool):
        return jsonify({"error": "is_correct must be true or false."}), 400

    fb = Feedback.query.filter_by(scan_id=scan.id).first()
    if fb:
        fb.is_correct = is_correct
    else:
        fb = Feedback(scan_id=scan.id, user_id=current_user.id, is_correct=is_correct)
        db.session.add(fb)
    db.session.commit()
    return jsonify({"success": True, "is_correct": fb.is_correct})


@app.route("/api/scans/<int:scan_id>/report.pdf", methods=["GET"])
@login_required
def scan_report_pdf(scan_id):
    scan = current_user.scans.filter_by(id=scan_id).first()
    if not scan:
        return jsonify({"error": "Scan not found."}), 404
    meta = dict(scan.meta or {})
    meta["report_id"] = scan.id
    meta.setdefault("generated_at", scan.to_summary()["time"])
    try:
        pdf_bytes = build_report_pdf(sanitize_record(dict(scan.record or {})), scan.result, meta, scan.verification)
    except Exception as e:
        return jsonify({"error": f"Could not build the PDF report: {e}"}), 500
    slug = re.sub(r"[^a-z0-9]+", "-", (scan.title or "job-posting").lower()).strip("-")[:60] or "job-posting"
    return send_file(
        BytesIO(pdf_bytes), mimetype="application/pdf",
        as_attachment=True, download_name=f"sentinel-report-{slug}.pdf",
    )


if __name__ == "__main__":
    # Local development server. In production use gunicorn (see Dockerfile).
    _debug = (not IS_PRODUCTION) and os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=_debug, use_reloader=False,
            host=os.environ.get("SENTINEL_HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")))
