import io

import pytest

SCAM = {
    "title": "Data Entry Work From Home",
    "description": ("Earn $5000 weekly from home. Pay a registration fee to start. "
                    "Contact us on WhatsApp. No interview needed, guaranteed income."),
    "company_profile": "",
}
GOOD = {
    "title": "Senior Software Engineer",
    "company_profile": "Acme Corp builds payment infrastructure used by thousands of businesses.",
    "description": ("We are hiring a backend engineer to design and maintain scalable services, "
                    "work with product managers, and mentor junior developers on our growing team."),
    "requirements": "5+ years of experience with Python, SQL and cloud platforms.",
    "benefits": "Health insurance, paid leave, learning budget.",
    "has_company_logo": 1,
}


# ---------- red-flag rules ----------
def test_red_flag_rules_detect_scam_patterns(flask_app):
    flags = flask_app.red_flag_rules(SCAM)
    assert "Asks for a fee or deposit before hiring" in flags
    assert "Asks candidates to contact via WhatsApp / Telegram" in flags
    assert "Says no interview is needed" in flags
    assert "Promises guaranteed income or a job" in flags


def test_red_flag_rules_quiet_on_normal_posting(flask_app):
    assert flask_app.red_flag_rules(GOOD) == []


# ---------- auth ----------
def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json["status"] == "ok"


def test_api_requires_login(client):
    assert client.post("/api/predict", json=SCAM).status_code in (302, 401)
    assert client.get("/api/scans").status_code in (302, 401)


def test_register_login_logout_flow(client):
    r = client.post("/register", data={"username": "flowuser", "password": "secret12", "confirm": "secret12"})
    assert r.status_code == 302
    assert client.get("/api/account/whoami").json["username"] == "flowuser"
    client.get("/logout")
    assert client.get("/api/account/whoami").status_code in (302, 401)
    r = client.post("/login", data={"username": "flowuser", "password": "wrong"})
    assert r.status_code == 200 and b"Incorrect" in r.data


def test_login_does_not_redirect_to_other_sites(flask_app):
    c = flask_app.app.test_client()
    c.post("/register", data={"username": "redir1", "password": "secret12", "confirm": "secret12"})
    c.get("/logout")
    r = c.post("/login?next=https://evil.example.com/steal", data={"username": "redir1", "password": "secret12"})
    assert r.status_code == 302
    assert "evil.example.com" not in r.headers["Location"]


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


# ---------- predict ----------
def test_predict_flags_obvious_scam(auth_client):
    r = auth_client.post("/api/predict", json=SCAM)
    assert r.status_code == 200
    body = r.json
    assert body["label"] == "FRAUDULENT"
    assert body["fraud_probability"] > 50
    assert body["risk_factors"]
    assert 0 <= body["confidence"] <= 100


def test_predict_accepts_normal_posting(auth_client):
    body = auth_client.post("/api/predict", json=GOOD).json
    assert body["label"] == "LEGITIMATE"
    assert body["fraud_probability"] < 50


def test_predict_rejects_empty_input(auth_client):
    assert auth_client.post("/api/predict", json={}).status_code == 400


# ---------- SSRF protection through the real endpoint ----------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5000/api/health",
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost/",
    "file:///etc/passwd",
])
def test_import_url_refuses_internal_targets(auth_client, url):
    r = auth_client.post("/api/import-url", json={"url": url})
    assert r.status_code == 422
    assert "can't be imported" in r.json["error"]


# ---------- scan history isolation ----------
def test_scans_are_private_per_user(two_users):
    alice, bob = two_users
    result = alice.post("/api/predict", json=SCAM).json
    scan_id = alice.post("/api/scans", json={"record": SCAM, "result": result}).json["id"]
    assert alice.get(f"/api/scans/{scan_id}").status_code == 200
    assert bob.get(f"/api/scans/{scan_id}").status_code == 404
    assert bob.delete(f"/api/scans/{scan_id}").status_code == 404
    assert bob.post(f"/api/scans/{scan_id}/feedback", json={"is_correct": True}).status_code == 404
    assert bob.get("/api/scans").json["scans"] == []


def test_feedback_validation(auth_client):
    result = auth_client.post("/api/predict", json=SCAM).json
    sid = auth_client.post("/api/scans", json={"record": SCAM, "result": result}).json["id"]
    assert auth_client.post(f"/api/scans/{sid}/feedback", json={"is_correct": "yes"}).status_code == 400
    assert auth_client.post(f"/api/scans/{sid}/feedback", json={"is_correct": False}).json["is_correct"] is False


# ---------- batch scanner ----------
def _csv(text):
    return {"file": (io.BytesIO(text.encode("utf-8")), "jobs.csv")}


def test_batch_scan_scores_rows(auth_client):
    csv_text = (
        "title,description,company_profile\n"
        '"Data Entry Work From Home","Earn $5000 weekly. Pay registration fee. WhatsApp us. No interview needed.",""\n'
        '"Backend Engineer","We are hiring a backend engineer to build scalable services and mentor developers on a growing team.","Acme builds payment infrastructure."\n'
        ',,\n'
    )
    r = auth_client.post("/api/batch-scan", data=_csv(csv_text), content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.json
    assert body["summary"]["total_rows"] == 3
    assert body["summary"]["scored"] == 2
    assert body["summary"]["skipped"] == 1
    rows = {x["row"]: x for x in body["rows"]}
    assert rows[1]["label"] == "FRAUDULENT" and rows[1]["red_flags"]
    assert rows[2]["risk_level"] in ("Low", "Medium")
    assert rows[3]["label"] == "SKIPPED"


def test_batch_scan_can_save_to_history(auth_client):
    csv_text = "job_title,job_description\nData Entry,Pay a registration fee first. No interview needed. Guaranteed income.\n"
    data = _csv(csv_text)
    data["save"] = "1"
    r = auth_client.post("/api/batch-scan", data=data, content_type="multipart/form-data")
    assert r.json["summary"]["saved_to_history"] == 1
    scans = auth_client.get("/api/scans").json["scans"]
    assert len(scans) == 1
    full = auth_client.get(f"/api/scans/{scans[0]['id']}").json
    assert full["meta"]["extraction_method"] == "batch CSV upload"
    assert isinstance(full["result"]["fraud_words"], list)         # UI-safe shape


@pytest.mark.parametrize("payload,fragment", [
    ({}, "Choose a CSV"),
    ({"file": (io.BytesIO(b"x"), "jobs.txt")}, "Only .csv"),
    (_csv("foo,bar\n1,2\n"), "title"),
    (_csv("title,description\n"), "no data rows"),
])
def test_batch_scan_validation(auth_client, payload, fragment):
    r = auth_client.post("/api/batch-scan", data=payload, content_type="multipart/form-data")
    assert r.status_code == 400
    assert fragment.lower() in r.json["error"].lower()


def test_batch_scan_row_limit(flask_app, auth_client, monkeypatch):
    monkeypatch.setattr(flask_app, "BATCH_MAX_ROWS", 3)
    rows = "\n".join(f"Job {i},Some description number {i}" for i in range(5))
    r = auth_client.post("/api/batch-scan", data=_csv("title,description\n" + rows),
                         content_type="multipart/form-data")
    assert r.status_code == 400 and "limit" in r.json["error"].lower()


def test_batch_requires_login(client):
    r = client.post("/api/batch-scan", data=_csv("title,description\na,b\n"), content_type="multipart/form-data")
    assert r.status_code in (302, 401)


# ---------- rate limiting through the real app ----------
def test_rate_limit_returns_429(flask_app, auth_client, monkeypatch):
    from security import limiter
    limiter.reset()
    monkeypatch.setattr(flask_app, "RATE_LIMITS_ON", True)
    codes = [auth_client.post("/api/import-url", json={"url": "http://127.0.0.1/"}).status_code for _ in range(25)]
    assert 429 in codes
    assert codes[0] == 422                    # first calls are processed normally
    limiter.reset()


# ---------- decision threshold ----------
def test_decision_threshold_changes_verdict(flask_app, monkeypatch):
    from security import limiter  # noqa: F401
    rec = flask_app.sanitize_record(dict(SCAM))
    monkeypatch.setattr(flask_app, "DECISION_THRESHOLD", 0.01)
    assert flask_app.score_record(rec, explain=False)["label"] == "FRAUDULENT"
    monkeypatch.setattr(flask_app, "DECISION_THRESHOLD", 0.999)
    out = flask_app.score_record(rec, explain=False)
    assert out["label"] == "LEGITIMATE"
    assert out["confidence"] == pytest.approx(100 - out["fraud_probability"], abs=0.11)


def test_bundled_model_has_tuned_threshold(flask_app):
    thr = flask_app.MODEL_INFO.get("decision_threshold")
    assert thr is None or 0.01 <= thr <= 0.99


def test_risk_level_bands(flask_app):
    assert flask_app.risk_level(85) == "High"
    assert flask_app.risk_level(55) == "Medium"
    assert flask_app.risk_level(5) == "Low"
