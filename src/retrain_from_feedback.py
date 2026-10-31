
import argparse
import json
import os
import sys

import joblib
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.svm import LinearSVC

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)  # lets "from app import app" and "from src.train import ..." work

from src.train import (  # reuse the EXACT same cleaning / feature pipeline as train.py
    TEXT_FIELDS, CATEGORICAL_FIELDS, BINARY_FIELDS, RANDOM_STATE,
    DATA_PATH, MODELS_DIR, clean_text, load_and_clean, build_features, evaluate_model,
)

# Add an entry here if your best model is ever something other than these five
# (hyperparameters copied straight from src/train.py so retraining matches it exactly).
FEEDBACK_MODEL_MAP = {
    "Logistic Regression": lambda: LogisticRegression(max_iter=2000, class_weight="balanced", C=3, random_state=RANDOM_STATE),
    "Naive Bayes": lambda: MultinomialNB(alpha=0.3),
    "Random Forest": lambda: RandomForestClassifier(n_estimators=300, class_weight="balanced_subsample", random_state=RANDOM_STATE, n_jobs=-1),
    "Linear SVM": lambda: CalibratedClassifierCV(LinearSVC(class_weight="balanced", C=0.5, random_state=RANDOM_STATE, max_iter=5000), cv=3),
    "Gradient Boosting": lambda: GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=RANDOM_STATE),
}


def scan_record_to_row(record: dict, fraudulent_label: int) -> dict:
    """Map a Scan.record dict (same fields the Analyze form submits) to a
    row shaped like the EMSCAD CSV columns train.py expects."""
    row = {field: record.get(field, "") for field in TEXT_FIELDS}
    for field in CATEGORICAL_FIELDS:
        row[field] = record.get(field) or "Unknown"
    for field in BINARY_FIELDS:
        row[field] = int(bool(record.get(field, 0)))
    row["salary_range"] = record.get("salary_range", "")
    row["fraudulent"] = fraudulent_label
    return row


def load_feedback_rows() -> pd.DataFrame:
    """Pull every scan with feedback from the database and turn each into
    a corrected training row."""
    from app import app  # importing app.py already puts src/ on sys.path
    from models import Feedback  # same module name app.py itself uses — avoids a duplicate model registration

    with app.app_context():
        feedbacks = Feedback.query.all()
        rows = []
        for fb in feedbacks:
            scan = fb.scan  # backref defined in src/models.py
            if scan is None or not scan.record or not scan.result:
                continue
            predicted = scan.result.get("prediction")
            if predicted is None:
                continue
            true_label = predicted if fb.is_correct else (1 - predicted)
            rows.append(scan_record_to_row(scan.record, true_label))
        print(f"Loaded {len(rows)} usable feedback row(s) from {len(feedbacks)} feedback record(s) in the database.")
        return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                         help="Overwrite models/*.pkl with the retrained model. Without this flag, only prints a comparison.")
    args = parser.parse_args()

    # ---- Current live metrics (what model_info.json already reports) ----
    info_path = os.path.join(MODELS_DIR, "model_info.json")
    with open(info_path) as f:
        old_info = json.load(f)
    best_name = old_info["best_model"]
    old_metrics = old_info["model_comparison"][best_name]
    print(f"Current live model: {best_name}")
    print("Current metrics:", {k: round(v, 4) for k, v in old_metrics.items()})

    if best_name not in FEEDBACK_MODEL_MAP:
        print(f"Don't know how to rebuild model type '{best_name}'. Add it to FEEDBACK_MODEL_MAP.")
        return

    # ---- Same train/test split train.py uses, on the original dataset ----
    print("\nLoading original EMSCAD dataset...")
    df = load_and_clean(DATA_PATH)
    train_df, test_df = train_test_split(
        df, test_size=0.2, random_state=RANDOM_STATE, stratify=df["fraudulent"]
    )
    # Feedback rows are added to TRAIN only, never to test — keeps this
    # comparison apples-to-apples against the metrics already saved.
    y_test = test_df["fraudulent"].values

    # ---- Feedback rows ----
    fb_df = load_feedback_rows()
    if fb_df.empty:
        print("\nNo usable feedback rows yet — nothing to retrain on. "
              "Answer a few 'Was this prediction correct?' prompts first.")
        return

    for col in TEXT_FIELDS:
        fb_df[col] = fb_df[col].fillna("")
    for col in CATEGORICAL_FIELDS:
        fb_df[col] = fb_df[col].fillna("Unknown").replace("", "Unknown")
    for col in BINARY_FIELDS:
        fb_df[col] = fb_df[col].fillna(0).astype(int)
    fb_df["combined_text"] = fb_df[TEXT_FIELDS].agg(" ".join, axis=1).apply(clean_text)
    fb_df["text_length"] = fb_df["combined_text"].apply(len)
    fb_df["has_salary"] = fb_df["salary_range"].astype(str).str.strip().ne("").astype(int)

    augmented_train = pd.concat([train_df, fb_df], ignore_index=True)
    print(f"\nTraining rows: {len(train_df)} original + {len(fb_df)} from feedback = {len(augmented_train)}")

    # ---- Refit vectorizer/encoder on the augmented train set, retrain ----
    X_train, vectorizer, encoder = build_features(augmented_train, fit=True)
    X_test, _, _ = build_features(test_df, vectorizer=vectorizer, encoder=encoder, fit=False)
    y_train = augmented_train["fraudulent"].values

    print(f"\nRetraining {best_name} on the augmented data...")
    model = FEEDBACK_MODEL_MAP[best_name]()
    model.fit(X_train, y_train)

    results = {}
    new_metrics = evaluate_model(best_name, model, X_test, y_test, results)

    # ---- Side-by-side comparison ----
    print("\n" + "=" * 50)
    print(f"{'Metric':<12}{'Old':>10}{'New':>10}{'Change':>10}")
    for k in old_metrics:
        old_v, new_v = old_metrics[k], new_metrics[k]
        print(f"{k:<12}{old_v:>10.4f}{new_v:>10.4f}{new_v - old_v:>+10.4f}")
    print("=" * 50)

    if not args.apply:
        print("\nDry run only — models/*.pkl were NOT changed. "
              "Re-run with --apply once you're happy with the numbers above.")
        return

    if new_metrics["f1"] < old_metrics["f1"]:
        confirm = input(
            f"\nNew F1 ({new_metrics['f1']:.4f}) is LOWER than the current model's "
            f"({old_metrics['f1']:.4f}). Overwrite anyway? [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Cancelled. models/*.pkl left unchanged.")
            return

    print("\nSaving new model artifacts (your models_backup/ already has the pre-retrain copies)...")
    joblib.dump(model, os.path.join(MODELS_DIR, "model.pkl"))
    joblib.dump(vectorizer, os.path.join(MODELS_DIR, "tfidf.pkl"))
    joblib.dump(encoder, os.path.join(MODELS_DIR, "encoder.pkl"))

    old_info["model_comparison"][best_name] = new_metrics
    old_info["dataset_rows"] = int(len(augmented_train) + len(test_df))
    old_info["feedback_rows_used"] = int(len(fb_df))
    with open(info_path, "w") as f:
        json.dump(old_info, f, indent=2)

    print("Done. model.pkl / tfidf.pkl / encoder.pkl / model_info.json updated.")


if __name__ == "__main__":
    main()