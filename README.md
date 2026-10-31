# Sentinel — AI Job Posting Fraud Scanner

A machine-learning web application that detects fraudulent job postings.
Built as an academic project demonstrating a complete, end-to-end ML system:
data pipeline → model training/evaluation → REST API → interactive web UI →
browser extension.

## Features

- **Two ways to analyze a posting**
  - **Manual entry** — fill in the job title, description, requirements, etc.
  - **Import from a link or pasted text** — paste a URL (LinkedIn, Indeed,
    Naukri, Apna, Greenhouse, Lever, or a company careers page) and Sentinel
    extracts the job details from the site's structured job-posting data
    (schema.org JobPosting markup, the same data used by Google Jobs). If a
    site blocks automated access, you can paste the job text instead.
- **ML-backed verdict** with a confidence gauge and a legitimate/fraudulent
  probability breakdown.
- **Explainability** (`src/explain.py`), two layers:
  - **Text**: Shapley values from SHAP's Partition explainer with a text
    masker. The model is treated as a black box (`predict_proba` only), so
    it works with any classifier. A Logistic-Regression-coefficient
    approximation is kept as an offline fallback if SHAP isn't installed.
  - **Structured fields**: counterfactual ablation — each structured field
    is reset to a neutral baseline one at a time and the swing in fraud
    probability is reported in percentage points.
- **Highlighted signal words** — the words the model found influential are
  highlighted directly inside the job description, requirements, benefits
  and company profile in the Detailed Report (red = pushes toward
  fraudulent, green = pushes toward legitimate).
- **Two risk-signal layers shown next to the ML verdict**
  - Basic checks: missing logo, missing company profile, remote-only, very
    short description, suspicious salary wording.
  - Rule-based red flags (`red_flag_rules` in `app.py`): upfront fee or
    deposit requests, WhatsApp/Telegram contact requests, guaranteed-income
    claims, unrealistic daily earnings, "no interview needed", and requests
    for OTP or bank details. These add risk signals only and do not change
    the model's verdict.
- **Persistent, multi-user backend** — accounts (Flask-Login) and a database
  (SQLAlchemy, `src/models.py`). It uses a **Neon (Postgres)** database when
  `DATABASE_URL` is set in `.env`, and falls back to local SQLite otherwise.
  Every scan is saved against the logged-in account, and users never see
  each other's scans. A `demo` / `demo1234` account is created automatically
  on first run.
- **External verification layer** (`src/verify.py`) — extracts company
  domains and recruiter emails from the posting and checks live whether the
  domain responds over HTTP, how old it is (WHOIS), and whether the email
  domain has a mail server (MX record) and is not a free provider. Runs as
  its own endpoint (`POST /api/verify`) so network delays never block the
  ML verdict. A failed lookup is reported as "couldn't verify", never as a
  guess.
- **Model Insights tab** — accuracy, precision, recall, F1, ROC-AUC, a model
  comparison across 5 algorithms, confusion matrix, ROC curves, dataset
  charts, and a card showing how much user feedback has been collected.
- **Scan history** — saved per account, with search by job title, a
  Fraudulent/Legitimate filter, a "Showing X of Y" counter, a detailed
  report and PDF export for any past scan. Times are shown in the local
  timezone of the computer running the app.
- **User feedback** — a logged-in user can mark a past scan's prediction as
  "correct" or "incorrect" (`POST /api/scans/<id>/feedback`). The script
  `src/retrain_from_feedback.py` can use this feedback to retrain the model.
- **Analytics dashboard** (`GET /api/dashboard-stats`) — 14-day scan and
  fraud trend, verdict split, most common risk signals, top
  fraud-indicating words, and a fraud breakdown by employment type. Charts
  are hand-written inline SVG, so the dashboard works offline.
- **Browser extension** (`extension/`) — adds a "Scan with Sentinel" button
  on job pages. It reads the page's structured job data from the live,
  logged-in page instead of fetching the URL from the server, which avoids
  the anti-bot blocks that stop the URL importer. Enabled sites: LinkedIn,
  Indeed, Naukri, Apna, Foundit, Shine, Internshala, Glassdoor and
  Wellfound (see the limitations section for what has been tested). See
  `extension/README.md`.
- Custom dark UI in plain HTML/CSS/JS, served by a Flask backend. Runs on
  **localhost**.

---

## Project structure

```
fake-job-posting-detector/
├── app.py                  # Flask backend + REST API + red-flag rules
├── requirements.txt
├── .env.example            # Copy to .env and add DATABASE_URL (optional)
├── data/
│   └── fake_job_postings.csv   # EMSCAD dataset (17,880 postings)
├── src/
│   ├── train.py                 # Training pipeline (EDA, features, model comparison)
│   ├── retrain_from_feedback.py # Retrain using corrections collected from users
│   ├── preprocessing.py         # Text-cleaning / feature-building (train + inference)
│   ├── explain.py               # SHAP text explanation + structured-field impact
│   ├── verify.py                # Domain/email WHOIS, DNS-MX, HTTP reachability
│   ├── security.py              # SSRF guard, safe redirects, rate limiter
│   ├── evaluate_cv.py           # Cross-validation + threshold tuning
│   ├── models.py                # SQLAlchemy models: User, Scan, Feedback
│   ├── derive.py                # Derives fields a job page doesn't expose; sanitize_record
│   ├── report_pdf.py            # Printable PDF reports (reportlab)
│   └── scraper.py               # URL / JSON-LD / pasted text -> job posting
├── models/                  # Saved model artifacts (generated by train.py)
│   ├── model.pkl
│   ├── tfidf.pkl
│   ├── encoder.pkl
│   ├── lr_explainer.pkl
│   └── model_info.json
├── assets/                  # Generated charts used by the UI
├── templates/               # index.html, login.html, register.html
├── static/
│   ├── css/style.css
│   └── js/app.js
└── extension/               # Browser extension (Chrome / Edge)
    ├── manifest.json
    ├── background.js
    ├── content.js
    └── popup.html / popup.js
```

---

## How it works

1. **Data**: [EMSCAD job postings dataset](https://www.kaggle.com/datasets/shivamb/real-or-fake-fake-jobposting-prediction)
   — 17,880 real job postings, 866 labeled fraudulent.
2. **Preprocessing**: HTML stripping, lowercasing, URL/punctuation removal on
   the text fields (title, company profile, description, requirements,
   benefits).
3. **Feature engineering**:
   - TF-IDF (unigrams + bigrams, 6,000 features) on the combined text.
   - One-hot encoding of structured fields (employment type, experience,
     education, industry, function).
   - Binary flags (telecommuting, has company logo, has screening questions,
     has salary listed).
4. **Model training & comparison** (`src/train.py`): Logistic Regression,
   Multinomial Naive Bayes, Random Forest, Linear SVM (calibrated for
   probabilities) and Gradient Boosting are trained on an 80/20 stratified
   split and compared on accuracy, precision, recall, F1 and ROC-AUC. The
   best model by F1 is selected automatically.
5. **Explainability**: computed per prediction on demand (SHAP for words,
   counterfactual ablation for structured fields). The API response reports
   which method actually ran (`explanation_method`).
6. **Serving**: Flask loads the saved model once at startup and exposes:
   - `POST /api/predict` — classify a posting (with explanations and risk signals)
   - `POST /api/import-url` — import a posting from a URL
   - `POST /api/parse-text` — import a posting from pasted text
   - `POST /api/extension/import-jsonld` — used by the browser extension
   - `POST /api/verify` — external domain/email verification
   - `POST /api/batch-scan` — score up to 200 postings from a CSV
   - `GET /api/health` — liveness probe
   - `POST /api/report/pdf` — render a report as a PDF
   - `GET/POST/PATCH/DELETE /api/scans...` — saved scan history
   - `POST /api/scans/<id>/feedback` — mark a prediction correct/incorrect
   - `GET /api/dashboard-stats` — analytics for the Dashboard tab
   - `GET /api/model-info` — metrics and feedback counts for Model Insights
   - `GET /api/account/whoami`, `GET/POST /api/account/token` — extension login

The current best model is a **Linear SVM**. See `assets/evaluation_report.txt`
for the latest accuracy, precision, recall, F1 and ROC-AUC, and the
per-model comparison.

---

## Setup & running locally

**Requirements:** Python 3.9+

```bash
# 1. Create and activate a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate          # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Retrain the model from scratch — trained artifacts are
#    already included in /models
python src/train.py

# 4. Run the app
python app.py
```

Open **http://127.0.0.1:5000** and log in with **demo** / **demo1234** (development only), or
register your own account.

### Optional: use an online Neon database instead of local SQLite

1. Create a free project at [neon.tech](https://neon.tech) and copy the
   connection string from the **Connect** button.
2. Copy `.env.example` to `.env` (next to `app.py`) and set:
   `DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require`
3. Run `python app.py`. The terminal prints
   `Database: online (DATABASE_URL)`. If it prints `local SQLite`, the
   `.env` file is not being read (check its name and location).

Never commit `.env` to Git — it contains your database password.

### Optional: browser extension

1. Open `edge://extensions` (or `chrome://extensions`), turn on Developer
   mode, and choose **Load unpacked**, then select the `extension` folder.
2. In the web app, open the **About** tab and copy your API token.
3. Click the extension icon, paste the token, keep the backend URL as
   `http://127.0.0.1:5000`, and press **Test connection**.
4. Open a job on a supported site and click **Scan with Sentinel**.

The app must be running locally for the extension to work.

---

## Notes & limitations

- The URL importer relies on structured job data (JSON-LD). Sites that block
  automated requests will ask you to paste the job text instead; the manual
  form and the pasted-text importer always work.
- The extension has been tested on **Apna**. The other listed sites are
  enabled in `manifest.json`, but not every site publishes structured job
  data, so results may vary until each one is tested.
- The dashboard groups scans by UTC date.
- Set `SENTINEL_SECRET_KEY` and `SENTINEL_ENV=production` for any real deployment (see **Deploying**).
- This tool gives a statistical prediction based on patterns in historical
  data. It is a **decision-support aid**, not a guarantee. Always verify an
  employer independently before sharing personal information or applying.


---

## What's new in v2

| Area | Change |
|---|---|
| **Batch scanning** | New **Batch** tab and `POST /api/batch-scan`: upload a CSV (max 200 rows), get a verdict, fraud probability, risk band (High / Medium / Low) and rule-based red flags per row. Optionally save every row to History; export results as CSV (formula-injection safe). |
| **Honest evaluation** | `src/evaluate_cv.py` runs 5-fold stratified cross-validation (features re-fit inside each fold), reports precision / recall / F1 / ROC-AUC / **PR-AUC** as mean ± std, and sweeps the decision threshold. Output: `assets/cv_report.txt`, `assets/pr_curve.png`. |
| **Tuned decision threshold** | The live model now uses a tuned threshold (stored in `models/model_info.json`) instead of a fixed 0.5. Override at runtime with `SENTINEL_DECISION_THRESHOLD`. |
| **SSRF protection** | The URL importer only fetches public `http(s)` addresses: private/loopback/link-local/cloud-metadata IPs are rejected, every redirect hop is re-validated, redirects and response size are capped. |
| **Hardening** | No hard-coded secret key (random per-process key + warning if unset), `.env` is now read *before* settings, `Secure`/`HttpOnly`/`SameSite` cookies, security headers, open-redirect fix on `?next=`, request-size cap, per-IP / per-user rate limiting (HTTP 429), demo account disabled in production. |
| **Ops** | `Dockerfile` (non-root, gunicorn, healthcheck), `docker-compose.yml`, `GET /api/health`, GitHub Actions CI, 55 automated tests. |

### Measured model performance (read this before quoting numbers)

The original single 80/20 split reported F1 = 0.873 for the Linear SVM. Cross-validation shows that
was a little optimistic. With only 866 fraud postings (4.8%), **accuracy is not a useful headline**
(a model that always answers "legitimate" scores 95%). Use these instead (fraud class, 5-fold CV):

| | Precision | Recall | F1 | PR-AUC |
|---|---|---|---|---|
| Linear SVM @ 0.50 threshold | 0.928 ± 0.026 | 0.752 ± 0.039 | 0.830 ± 0.025 | 0.902 |
| Linear SVM @ tuned threshold 0.366 (live) | 0.881 | 0.819 | **0.849** | 0.902 |
| Logistic Regression @ 0.50 | 0.687 ± 0.028 | 0.901 ± 0.020 | 0.779 ± 0.017 | 0.894 |

Trade-off: the tuned threshold finds about 7 more scams per 100 at the cost of a few more false
alarms. For a "catch almost every scam" mode run
`python src/evaluate_cv.py --apply --objective recall --target-recall 0.90`
(recall 0.90, precision about 0.67).

**Known limitation:** the EMSCAD dataset is English and mostly Western postings from several years
ago. Results on Indian job portals are untested; building a small hand-labelled test set from those
sites is the best next step.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

55 tests cover the SSRF guard, open-redirect guard, rate limiter, red-flag rules, auth flow,
per-user data isolation, prediction, batch upload validation, and the decision threshold.

## Deploying

```bash
# 1. create .env from the example and set a real secret
cp .env.example .env            # then edit: SENTINEL_SECRET_KEY=<64 random hex chars>
# 2. run
docker compose up --build       # http://localhost:5000
```

For a public deployment (Render, Railway, Fly.io, Hugging Face Spaces, ...):
set `SENTINEL_ENV=production`, `SENTINEL_SECRET_KEY`, and `DATABASE_URL` (Postgres) as environment
variables on the host, serve over **https**, and remove `SENTINEL_COOKIE_SECURE: "0"` from
`docker-compose.yml`. The dataset (`data/fake_job_postings.csv`) is only needed for retraining and
is not part of the image. Rate limiting is per gunicorn worker; use Redis-backed limiting if you scale out.

### Security notes
* `.env` is git-ignored. If a database password or API key was ever committed or shared, rotate it.
* The login/register forms rely on `SameSite=Lax` cookies rather than CSRF tokens; add Flask-WTF if
  you expose the app on a shared domain.
* `safe_get` validates DNS before connecting; for a hardened deployment also block outbound access
  to internal networks at the firewall.

---

## What I changed

The first two items came from an updated version of the project that I
merged in. Items 3 to 12 are my own additions.

**Merged from the updated version**

1. Database can be moved to Neon (managed Postgres) through a small `.env`
   reader in `app.py` plus `psycopg2-binary`, falling back to local SQLite
   if `DATABASE_URL` isn't set.
2. `src/derive.py`: `clean_company_profile` and `sanitize_record`, applied on
   the predict/report/scan routes; the browser extension's `content.js` also
   cleans scraped company text (limit raised from 2,500 to 6,000
   characters).

**My additions**

3. Extended the browser extension to run on more sites (LinkedIn, Indeed,
   Naukri, Apna, Foundit, Shine, Internshala, Glassdoor, Wellfound).
   Tested on Apna; the others are enabled but not yet tested.
4. `src/scraper.py`: added `_to_text()` and made `_strip_html()` handle
   lists and dicts, which fixed a crash on Apna's JSON-LD structure.
5. `app.js` / `style.css`: `highlightWords()` highlights influential words
   directly inside the job-posting text in the Detailed Report (red for
   fraud-indicating, green for legitimate-indicating).
6. `src/models.py`: `_local_str()` converts stored UTC scan timestamps to the
   local timezone of the computer running the app, for History and PDF
   reports.
7. Added a rule-based red-flag layer (`red_flag_rules` in `app.py`) that
   detects common scam patterns (upfront fees/deposits, WhatsApp/Telegram
   contact requests, guaranteed-income claims, unrealistic daily earnings,
   "no interview needed", requests for OTP/bank details) and shows them as
   risk factors next to the model's own explanation.
8. History tab: added a search box (by job title), a Fraudulent/Legitimate
   filter, and a "Showing X of Y" counter.
9. Retrained the model with the installed scikit-learn version to remove a
   version-mismatch warning.
10. Added a `Feedback` table and a `POST /api/scans/<id>/feedback` endpoint
    so a logged-in user can mark any past scan as "correct" or "incorrect".
11. Added `src/retrain_from_feedback.py`: reads the collected feedback,
    builds corrected training rows, retrains the current best model on the
    original dataset plus the corrections, and prints an old-vs-new metrics
    comparison before optionally overwriting the live model (`--apply`).
12. Model Insights tab: `/api/model-info` now also reports live feedback
    counts, and the UI shows a new "Feedback collected" card.

---

## Possible extensions (future work)

- Multi-language support for non-English postings
- Testing and fixing the extension on every listed job site
- Admin page with overall statistics
- Chrome Web Store packaging (signed, auto-updating) instead of "load unpacked"