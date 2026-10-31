
import re
import pandas as pd
from scipy.sparse import hstack, csr_matrix

TEXT_FIELDS = ["title", "company_profile", "description", "requirements", "benefits"]
CATEGORICAL_FIELDS = [
    "employment_type", "required_experience", "required_education",
    "industry", "function",
]
BINARY_FIELDS = ["telecommuting", "has_company_logo", "has_questions"]


def clean_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.lower()
    text = re.sub(r"http\S+|www\.\S+", " URL ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def record_to_frame(record: dict) -> pd.DataFrame:
    """Turn a single job-posting dict into a one-row DataFrame matching training schema."""
    row = {}
    for f in TEXT_FIELDS:
        row[f] = record.get(f, "") or ""
    combined = " ".join(clean_text(row[f]) for f in TEXT_FIELDS)
    row["combined_text"] = combined
    for f in CATEGORICAL_FIELDS:
        val = record.get(f) or "Unknown"
        row[f] = val
    for f in BINARY_FIELDS:
        val = record.get(f, 0)
        row[f] = int(bool(val)) if val not in (None, "", "Unknown") else 0
    row["has_salary"] = 1 if record.get("salary_range") else 0
    return pd.DataFrame([row])


def build_feature_vector(df: pd.DataFrame, vectorizer, encoder):
    X_text = vectorizer.transform(df["combined_text"])
    X_cat = encoder.transform(df[CATEGORICAL_FIELDS])
    X_bin = csr_matrix(df[BINARY_FIELDS + ["has_salary"]].values.astype(float))
    return hstack([X_text, X_cat, X_bin]).tocsr()
