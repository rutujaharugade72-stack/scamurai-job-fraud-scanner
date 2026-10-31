
import json
import os
import re
import warnings

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.sparse import hstack, csr_matrix
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, roc_curve, confusion_matrix, classification_report
)
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.preprocessing import OneHotEncoder
from sklearn.svm import LinearSVC
from sklearn.calibration import CalibratedClassifierCV

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "fake_job_postings.csv")
MODELS_DIR = os.path.join(BASE_DIR, "models")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

RANDOM_STATE = 42
TEXT_FIELDS = ["title", "company_profile", "description", "requirements", "benefits"]
CATEGORICAL_FIELDS = [
    "employment_type", "required_experience", "required_education",
    "industry", "function",
]
BINARY_FIELDS = ["telecommuting", "has_company_logo", "has_questions"]


def clean_text(text: str) -> str:
    """Lowercase, strip URLs/HTML/punctuation, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", text)          # strip HTML tags
    text = text.lower()
    text = re.sub(r"http\S+|www\.\S+", " URL ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_and_clean(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in TEXT_FIELDS:
        df[col] = df[col].fillna("")
    for col in CATEGORICAL_FIELDS:
        df[col] = df[col].fillna("Unknown").replace("", "Unknown")
    for col in BINARY_FIELDS:
        df[col] = df[col].fillna(0).astype(int)
    df["combined_text"] = df[TEXT_FIELDS].agg(" ".join, axis=1).apply(clean_text)
    df["text_length"] = df["combined_text"].apply(len)
    df["has_salary"] = df["salary_range"].notna().astype(int) if "salary_range" in df.columns else 0
    return df


def run_eda(df: pd.DataFrame):
    """Save exploratory-analysis charts to assets/."""
    sns.set_theme(style="whitegrid")

    # Class balance
    plt.figure(figsize=(5, 4))
    counts = df["fraudulent"].value_counts().sort_index()
    plt.bar(["Legitimate", "Fraudulent"], counts.values, color=["#2ecc71", "#e74c3c"])
    for i, v in enumerate(counts.values):
        plt.text(i, v + 200, str(v), ha="center", fontweight="bold")
    plt.title("Class Distribution")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "class_distribution.png"), dpi=140)
    plt.close()

    # Text length by class
    plt.figure(figsize=(6, 4))
    sns.boxplot(x="fraudulent", y="text_length", data=df[df["text_length"] < df["text_length"].quantile(0.95)])
    plt.xticks([0, 1], ["Legitimate", "Fraudulent"])
    plt.title("Posting Text Length by Class")
    plt.ylabel("Character count")
    plt.xlabel("")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "text_length.png"), dpi=140)
    plt.close()

    # Missing company logo vs fraud rate
    plt.figure(figsize=(5, 4))
    rate = df.groupby("has_company_logo")["fraudulent"].mean() * 100
    plt.bar(["No Logo", "Has Logo"], rate.values, color=["#e67e22", "#3498db"])
    plt.title("Fraud Rate by Company Logo Presence")
    plt.ylabel("Fraud rate (%)")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "logo_vs_fraud.png"), dpi=140)
    plt.close()


def build_features(df: pd.DataFrame, vectorizer=None, encoder=None, fit=True):
    """Combine TF-IDF text features with encoded structured features."""
    if fit:
        vectorizer = TfidfVectorizer(
            max_features=6000, ngram_range=(1, 2), stop_words="english",
            min_df=3, sublinear_tf=True
        )
        X_text = vectorizer.fit_transform(df["combined_text"])
    else:
        X_text = vectorizer.transform(df["combined_text"])

    cat_data = df[CATEGORICAL_FIELDS]
    if fit:
        encoder = OneHotEncoder(handle_unknown="ignore", min_frequency=15, sparse_output=True)
        X_cat = encoder.fit_transform(cat_data)
    else:
        X_cat = encoder.transform(cat_data)

    X_bin = csr_matrix(df[BINARY_FIELDS + ["has_salary"]].values.astype(float))
    X = hstack([X_text, X_cat, X_bin]).tocsr()
    return X, vectorizer, encoder


def evaluate_model(name, model, X_test, y_test, results, proba_source=None):
    proba_model = proba_source if proba_source is not None else model
    y_pred = model.predict(X_test)
    y_proba = proba_model.predict_proba(X_test)[:, 1]

    metrics = {
        "accuracy": accuracy_score(y_test, y_pred),
        "precision": precision_score(y_test, y_pred, zero_division=0),
        "recall": recall_score(y_test, y_pred, zero_division=0),
        "f1": f1_score(y_test, y_pred, zero_division=0),
        "roc_auc": roc_auc_score(y_test, y_proba),
    }
    results[name] = {"metrics": metrics, "y_pred": y_pred, "y_proba": y_proba}
    print(f"\n{name}")
    for k, v in metrics.items():
        print(f"  {k:10s}: {v:.4f}")
    return metrics


def main():
    print("Loading dataset...")
    df = load_and_clean(DATA_PATH)
    print(f"Rows: {len(df)} | Fraudulent: {df['fraudulent'].sum()} | Legit: {(df['fraudulent']==0).sum()}")

    print("Running EDA...")
    run_eda(df)

    print("Splitting data...")
    train_df, test_df = train_test_split(
        df, test_size=0.2, random_state=RANDOM_STATE, stratify=df["fraudulent"]
    )
    y_train, y_test = train_df["fraudulent"].values, test_df["fraudulent"].values

    print("Building features (TF-IDF + structured)...")
    X_train, vectorizer, encoder = build_features(train_df, fit=True)
    X_test, _, _ = build_features(test_df, vectorizer=vectorizer, encoder=encoder, fit=False)
    print(f"Feature matrix: {X_train.shape}")

    results = {}

    print("\nTraining Logistic Regression...")
    lr = LogisticRegression(max_iter=2000, class_weight="balanced", C=3, random_state=RANDOM_STATE)
    lr.fit(X_train, y_train)
    evaluate_model("Logistic Regression", lr, X_test, y_test, results)

    print("\nTraining Multinomial Naive Bayes...")
    nb = MultinomialNB(alpha=0.3)
    nb.fit(X_train, y_train)
    evaluate_model("Naive Bayes", nb, X_test, y_test, results)

    print("\nTraining Random Forest...")
    rf = RandomForestClassifier(
        n_estimators=300, max_depth=None, class_weight="balanced_subsample",
        random_state=RANDOM_STATE, n_jobs=-1
    )
    rf.fit(X_train, y_train)
    evaluate_model("Random Forest", rf, X_test, y_test, results)

    print("\nTraining Linear SVM (calibrated for probabilities)...")
    svm_base = LinearSVC(class_weight="balanced", C=0.5, random_state=RANDOM_STATE, max_iter=5000)
    svm = CalibratedClassifierCV(svm_base, cv=3)
    svm.fit(X_train, y_train)
    evaluate_model("Linear SVM", svm, X_test, y_test, results)

    print("\nTraining Gradient Boosting...")
    gb = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=RANDOM_STATE)
    gb.fit(X_train, y_train)
    evaluate_model("Gradient Boosting", gb, X_test, y_test, results)

    # Pick best model by F1 score (best balance for imbalanced fraud detection)
    best_name = max(results, key=lambda k: results[k]["metrics"]["f1"])
    best_model = {
        "Logistic Regression": lr, "Naive Bayes": nb, "Random Forest": rf,
        "Linear SVM": svm, "Gradient Boosting": gb,
    }[best_name]
    print(f"\n>>> Best model selected: {best_name}")

    # ---- Comparison chart ----
    plt.figure(figsize=(8, 5))
    names = list(results.keys())
    f1s = [results[n]["metrics"]["f1"] for n in names]
    colors = ["#9b59b6" if n == best_name else "#95a5a6" for n in names]
    bars = plt.barh(names, f1s, color=colors)
    plt.xlabel("F1 Score")
    plt.title("Model Comparison (F1 Score)")
    plt.xlim(0, 1)
    for bar, val in zip(bars, f1s):
        plt.text(val + 0.01, bar.get_y() + bar.get_height() / 2, f"{val:.3f}", va="center")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "model_comparison.png"), dpi=140)
    plt.close()

    # ---- Confusion matrix for best model ----
    cm = confusion_matrix(y_test, results[best_name]["y_pred"])
    plt.figure(figsize=(5, 4))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Purples",
                xticklabels=["Legit", "Fraud"], yticklabels=["Legit", "Fraud"])
    plt.title(f"Confusion Matrix — {best_name}")
    plt.ylabel("Actual")
    plt.xlabel("Predicted")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "confusion_matrix.png"), dpi=140)
    plt.close()

    # ---- ROC curves ----
    plt.figure(figsize=(6, 5))
    for name in names:
        fpr, tpr, _ = roc_curve(y_test, results[name]["y_proba"])
        auc = results[name]["metrics"]["roc_auc"]
        lw = 2.5 if name == best_name else 1.2
        plt.plot(fpr, tpr, label=f"{name} (AUC={auc:.3f})", linewidth=lw)
    plt.plot([0, 1], [0, 1], "k--", linewidth=1)
    plt.xlabel("False Positive Rate")
    plt.ylabel("True Positive Rate")
    plt.title("ROC Curves")
    plt.legend(fontsize=8, loc="lower right")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "roc_curves.png"), dpi=140)
    plt.close()

    # ---- Top predictive words (Logistic Regression coefficients) ----
    feature_names = vectorizer.get_feature_names_out()
    coefs = lr.coef_[0][: len(feature_names)]
    top_fraud_idx = np.argsort(coefs)[-15:][::-1]
    top_legit_idx = np.argsort(coefs)[:15]
    top_words = {
        "fraud_indicators": [(feature_names[i], float(coefs[i])) for i in top_fraud_idx],
        "legit_indicators": [(feature_names[i], float(coefs[i])) for i in top_legit_idx],
    }

    plt.figure(figsize=(7, 6))
    words = [w for w, _ in top_words["fraud_indicators"]]
    vals = [v for _, v in top_words["fraud_indicators"]]
    plt.barh(words[::-1], vals[::-1], color="#e74c3c")
    plt.title("Top Words Indicating a FRAUDULENT Posting")
    plt.xlabel("Logistic Regression coefficient weight")
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "top_fraud_words.png"), dpi=140)
    plt.close()

    # ---- Save everything ----
    print("\nSaving model artifacts...")
    joblib.dump(best_model, os.path.join(MODELS_DIR, "model.pkl"))
    joblib.dump(vectorizer, os.path.join(MODELS_DIR, "tfidf.pkl"))
    joblib.dump(encoder, os.path.join(MODELS_DIR, "encoder.pkl"))
    joblib.dump(lr, os.path.join(MODELS_DIR, "lr_explainer.pkl"))  # kept for word-level explainability

    model_info = {
        "dataset_rows": int(len(df)),
        "fraudulent_count": int(df["fraudulent"].sum()),
        "legitimate_count": int((df["fraudulent"] == 0).sum()),
        "test_size": 0.2,
        "random_state": RANDOM_STATE,
        "text_fields": TEXT_FIELDS,
        "categorical_fields": CATEGORICAL_FIELDS,
        "binary_fields": BINARY_FIELDS,
        "best_model": best_name,
        "model_comparison": {n: results[n]["metrics"] for n in names},
        "top_words": top_words,
    }
    with open(os.path.join(MODELS_DIR, "model_info.json"), "w") as f:
        json.dump(model_info, f, indent=2)

    with open(os.path.join(ASSETS_DIR, "evaluation_report.txt"), "w") as f:
        f.write("FAKE JOB POSTING DETECTION — MODEL EVALUATION REPORT\n")
        f.write("=" * 55 + "\n\n")
        f.write(f"Dataset: {len(df)} rows | Fraudulent: {df['fraudulent'].sum()} | Legitimate: {(df['fraudulent']==0).sum()}\n")
        f.write(f"Train/Test split: 80/20 (random_state={RANDOM_STATE})\n")
        f.write(f"Feature matrix shape: {X_train.shape}\n\n")
        for name in names:
            f.write(f"\n{name}\n" + "-" * len(name) + "\n")
            for k, v in results[name]["metrics"].items():
                f.write(f"  {k:10s}: {v:.4f}\n")
        f.write(f"\n\nBEST MODEL: {best_name}\n")
        f.write("\nClassification report (best model):\n")
        f.write(classification_report(y_test, results[best_name]["y_pred"], target_names=["Legit", "Fraud"]))

    print("Done. Artifacts saved to /models and /assets")


if __name__ == "__main__":
    main()
