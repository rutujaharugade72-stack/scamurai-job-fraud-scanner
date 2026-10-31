
import numpy as np
import pandas as pd
from scipy.sparse import hstack, csr_matrix

from preprocessing import CATEGORICAL_FIELDS, BINARY_FIELDS

MAX_EVALS = 300          # bounds SHAP's per-request compute budget
MAX_EXPLAIN_TOKENS = 300  # truncate very long postings before explaining (display already caps at top_k anyway)
MIN_STRUCTURED_DELTA = 0.5  # percentage points; below this we don't bother reporting a field

CATEGORICAL_LABELS = {
    "employment_type": "Employment type",
    "required_experience": "Required experience",
    "required_education": "Required education",
    "industry": "Industry",
    "function": "Job function",
}
BINARY_LABELS = {
    "telecommuting": "Telecommuting / remote",
    "has_company_logo": "Company logo shown",
    "has_questions": "Screening questions present",
}


# ---------------------------------------------------------------------
# Shared helper: build a "fixed everything except text" feature builder
# ---------------------------------------------------------------------
def _make_feature_builder(row: dict, vectorizer, encoder):
    """Returns a function batch_features(texts) -> sparse feature matrix,
    with every categorical/binary field pinned to `row`'s actual values."""
    cat_values = {f: row.get(f) or "Unknown" for f in CATEGORICAL_FIELDS}
    bin_values = {f: int(row.get(f, 0) or 0) for f in BINARY_FIELDS}
    has_salary = int(row.get("has_salary", 0) or 0)

    def batch_features(texts):
        n = len(texts)
        X_text = vectorizer.transform(texts)
        cat_df = pd.DataFrame([cat_values] * n)
        X_cat = encoder.transform(cat_df[CATEGORICAL_FIELDS])
        bin_row = [bin_values[f] for f in BINARY_FIELDS] + [has_salary]
        X_bin = csr_matrix(np.tile(np.array(bin_row, dtype=float), (n, 1)))
        return hstack([X_text, X_cat, X_bin]).tocsr()

    return batch_features


def _make_predict_fn(row: dict, vectorizer, encoder, model):
    batch_features = _make_feature_builder(row, vectorizer, encoder)

    def predict_fn(texts):
        X = batch_features(list(texts))
        return model.predict_proba(X)[:, 1]

    return predict_fn


# ---------------------------------------------------------------------
# 1. Text explanation
# ---------------------------------------------------------------------
def explain_text_with_shap(row: dict, vectorizer, encoder, model, top_k=8):
    import shap  # imported lazily so the whole app can still run without it installed

    text = row.get("combined_text", "") or ""
    tokens = text.split()
    truncated = len(tokens) > MAX_EXPLAIN_TOKENS
    if truncated:
        text = " ".join(tokens[:MAX_EXPLAIN_TOKENS])

    predict_fn = _make_predict_fn(row, vectorizer, encoder, model)
    masker = shap.maskers.Text(r"\W+")
    explainer = shap.Explainer(predict_fn, masker, silent=True)
    shap_values = explainer([text], max_evals=MAX_EVALS)

    exp = shap_values[0]
    raw_base = exp.base_values
    base_value = float(np.ravel(raw_base)[0]) if not np.isscalar(raw_base) else float(raw_base)

    # SHAP's text masker gives each *occurrence* of a word its own Shapley
    # value, so a word appearing 3 times yields 3 separate entries. Sum
    # them per lowercased word — Shapley values are additive, so this is
    # the word's true total contribution, not an approximation — and only
    # then take the top_k, otherwise a common word repeated in the
    # posting can crowd out the list with duplicates of itself.
    totals = {}
    for tok, val in zip(exp.data, exp.values):
        if not tok or not tok.strip():
            continue
        key = tok.strip().lower()
        totals[key] = totals.get(key, 0.0) + float(val)

    pairs = sorted(totals.items(), key=lambda p: p[1])
    legit_pairs = [p for p in pairs if p[1] < 0][:top_k]
    fraud_pairs = list(reversed([p for p in pairs if p[1] > 0]))[:top_k]

    return {
        "fraud_words": [w for w, _ in fraud_pairs],
        "legit_words": [w for w, _ in legit_pairs],
        "fraud_word_scores": [{"word": w, "impact": round(v, 4)} for w, v in fraud_pairs],
        "legit_word_scores": [{"word": w, "impact": round(v, 4)} for w, v in legit_pairs],
        "base_value": round(base_value, 4),
        "method": "shap-partition-text",
        "truncated": truncated,
    }


def explain_text_legacy_lr(df_row_text: str, vectorizer, lr_explainer, top_k=8):
    """Fallback used only if SHAP is unavailable or errors: LR-coefficient
    x TF-IDF-weight approximation. Faithful only insofar as the deployed
    model behaves like the LR explainer, which is why this is a fallback
    and not the primary method."""
    feature_names = vectorizer.get_feature_names_out()
    lr_coefs = lr_explainer.coef_[0][: len(feature_names)]
    X_text = vectorizer.transform([df_row_text])
    row = X_text.tocoo()
    contributions = [(feature_names[col], float(lr_coefs[col] * val)) for col, val in zip(row.col, row.data)]
    contributions.sort(key=lambda x: x[1])
    legit_pairs = [p for p in contributions[:top_k] if p[1] < 0]
    fraud_pairs = [p for p in contributions[::-1][:top_k] if p[1] > 0]
    return {
        "fraud_words": [w for w, _ in fraud_pairs],
        "legit_words": [w for w, _ in legit_pairs],
        "fraud_word_scores": [{"word": w, "impact": round(v, 4)} for w, v in fraud_pairs],
        "legit_word_scores": [{"word": w, "impact": round(v, 4)} for w, v in legit_pairs],
        "base_value": None,
        "method": "lr-coefficient-fallback",
        "truncated": False,
    }


# ---------------------------------------------------------------------
# 2. Structured-feature explanation (counterfactual ablation)
# ---------------------------------------------------------------------
def explain_structured_features(row: dict, vectorizer, encoder, model, min_delta_pp=MIN_STRUCTURED_DELTA):
    text = row.get("combined_text", "") or ""
    X_text = vectorizer.transform([text])  # fixed for every comparison below

    base_cat = {f: row.get(f) or "Unknown" for f in CATEGORICAL_FIELDS}
    base_bin = {f: int(row.get(f, 0) or 0) for f in BINARY_FIELDS}
    base_salary = int(row.get("has_salary", 0) or 0)

    def proba(cat_overrides=None, bin_overrides=None, salary_override=None):
        cat = {**base_cat, **(cat_overrides or {})}
        binv = {**base_bin, **(bin_overrides or {})}
        sal = base_salary if salary_override is None else salary_override
        cat_df = pd.DataFrame([cat])
        X_cat = encoder.transform(cat_df[CATEGORICAL_FIELDS])
        bin_row = [binv[f] for f in BINARY_FIELDS] + [sal]
        X_bin = csr_matrix(np.array([bin_row], dtype=float))
        X = hstack([X_text, X_cat, X_bin]).tocsr()
        return float(model.predict_proba(X)[0, 1])

    actual = proba()
    impacts = []

    for field, label in CATEGORICAL_LABELS.items():
        if base_cat[field] in ("Unknown", "", None):
            continue
        baseline = proba(cat_overrides={field: "Unknown"})
        delta_pp = round((actual - baseline) * 100, 2)
        if abs(delta_pp) >= min_delta_pp:
            impacts.append({
                "field": field, "label": label, "value": base_cat[field],
                "delta_pp": delta_pp, "direction": "fraud" if delta_pp > 0 else "legit",
            })

    for field, label in BINARY_LABELS.items():
        if base_bin[field] != 1:
            continue
        baseline = proba(bin_overrides={field: 0})
        delta_pp = round((actual - baseline) * 100, 2)
        if abs(delta_pp) >= min_delta_pp:
            impacts.append({
                "field": field, "label": label, "value": "Yes",
                "delta_pp": delta_pp, "direction": "fraud" if delta_pp > 0 else "legit",
            })

    if base_salary:
        baseline = proba(salary_override=0)
        delta_pp = round((actual - baseline) * 100, 2)
        if abs(delta_pp) >= min_delta_pp:
            impacts.append({
                "field": "has_salary", "label": "Salary range provided", "value": "Yes",
                "delta_pp": delta_pp, "direction": "fraud" if delta_pp > 0 else "legit",
            })

    impacts.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)
    return impacts


# ---------------------------------------------------------------------
# Public entry point used by app.py
# ---------------------------------------------------------------------
def explain(df, vectorizer, encoder, model, lr_explainer, top_k=8):
    """df: the one-row DataFrame produced by preprocessing.record_to_frame.
    Returns a dict merging the text explanation and the structured-feature
    impact list, always including a 'method' field describing how the
    word-level explanation was produced."""
    row = df.iloc[0].to_dict()

    try:
        result = explain_text_with_shap(row, vectorizer, encoder, model, top_k=top_k)
    except Exception as e:
        result = explain_text_legacy_lr(row["combined_text"], vectorizer, lr_explainer, top_k=top_k)
        result["fallback_reason"] = f"{type(e).__name__}: {e}"

    try:
        result["structured_impact"] = explain_structured_features(row, vectorizer, encoder, model)
    except Exception:
        result["structured_impact"] = []

    return result
