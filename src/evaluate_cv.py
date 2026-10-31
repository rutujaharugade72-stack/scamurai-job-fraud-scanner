"""Honest evaluation + decision-threshold tuning for Sentinel.

Why this exists
---------------
train.py picks the best model on ONE 80/20 split and reports that same split's
score. With only 866 fraud examples (4.8%), one split is noisy, and accuracy
(~99%) is misleading because a model that always says "legitimate" already
scores 95%. This script reports what actually matters:

  * stratified K-fold cross-validation (features are re-fit inside every fold,
    so no information leaks from the validation fold),
  * precision / recall / F1 / ROC-AUC and PR-AUC as mean +- std over folds,
  * a threshold sweep on the pooled out-of-fold probabilities, so you can pick
    the operating point you want (e.g. "catch at least 90% of scams").

Usage
-----
    python src/evaluate_cv.py                      # SVM + Logistic Regression, 5 folds
    python src/evaluate_cv.py --target-recall 0.90
    python src/evaluate_cv.py --apply              # store the best-F1 threshold in models/model_info.json
    python src/evaluate_cv.py --apply --objective recall --target-recall 0.90
                                                   # "catch more scams" mode (more false alarms)

Outputs: assets/cv_report.txt, assets/cv_report.json, assets/pr_curve.png
"""
import argparse
import json
import os
import sys
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold
from sklearn.svm import LinearSVC

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train  # noqa: E402  (reuses the exact same cleaning + feature code)

warnings.filterwarnings("ignore")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
MODELS_DIR = os.path.join(BASE_DIR, "models")
SEED = 42


def make_model(name):
    if name == "svm":
        return "Linear SVM", CalibratedClassifierCV(
            LinearSVC(class_weight="balanced", C=0.5, random_state=SEED, max_iter=5000), cv=3)
    if name == "lr":
        return "Logistic Regression", LogisticRegression(
            max_iter=2000, class_weight="balanced", C=3, random_state=SEED)
    raise SystemExit(f"Unknown model '{name}'. Use: svm, lr")


def fold_metrics(y, proba, threshold=0.5):
    pred = (proba >= threshold).astype(int)
    return {
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "f1": f1_score(y, pred, zero_division=0),
        "roc_auc": roc_auc_score(y, proba),
        "pr_auc": average_precision_score(y, proba),
    }


def cross_validate(df, model_key, folds):
    name, _ = make_model(model_key)
    y = df["fraudulent"].values
    oof = np.zeros(len(df))
    per_fold = []
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=SEED)
    for i, (tr, va) in enumerate(skf.split(df, y), start=1):
        tr_df, va_df = df.iloc[tr], df.iloc[va]
        X_tr, vec, enc = train.build_features(tr_df, fit=True)            # fit ONLY on the training fold
        X_va, _, _ = train.build_features(va_df, vectorizer=vec, encoder=enc, fit=False)
        _, model = make_model(model_key)
        model.fit(X_tr, y[tr])
        proba = model.predict_proba(X_va)[:, 1]
        oof[va] = proba
        m = fold_metrics(y[va], proba)
        per_fold.append(m)
        print(f"  [{name}] fold {i}/{folds}: P={m['precision']:.3f} R={m['recall']:.3f} "
              f"F1={m['f1']:.3f} PR-AUC={m['pr_auc']:.3f}")
    summary = {k: (float(np.mean([f[k] for f in per_fold])), float(np.std([f[k] for f in per_fold])))
               for k in per_fold[0]}
    return name, summary, oof


def sweep_thresholds(y, proba, target_recall):
    prec, rec, thr = precision_recall_curve(y, proba)
    prec, rec = prec[:-1], rec[:-1]                       # align with thr
    f1 = np.where(prec + rec > 0, 2 * prec * rec / (prec + rec + 1e-12), 0)
    best_f1 = int(np.argmax(f1))
    ok = np.where(rec >= target_recall)[0]
    if len(ok):
        pick = ok[int(np.argmax(prec[ok]))]                # best precision that still meets the recall goal
    else:
        pick = best_f1
    row = lambda i: {"threshold": float(thr[i]), "precision": float(prec[i]),
                     "recall": float(rec[i]), "f1": float(f1[i])}
    return row(best_f1), row(pick), (prec, rec, thr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--models", default="svm,lr", help="comma list from: svm, lr")
    ap.add_argument("--target-recall", type=float, default=0.90)
    ap.add_argument("--objective", choices=["f1", "recall"], default="f1",
                    help="which threshold --apply stores: 'f1' (balanced, default) or 'recall' "
                         "(catch >= --target-recall of scams, accepts more false alarms)")
    ap.add_argument("--apply", action="store_true",
                    help="write the chosen threshold for the live model into models/model_info.json")
    args = ap.parse_args()

    print("Loading dataset...")
    df = train.load_and_clean(train.DATA_PATH)
    y = df["fraudulent"].values
    print(f"Rows: {len(df)} | Fraud: {int(y.sum())} ({y.mean()*100:.1f}%)\n")

    report, curves = {}, {}
    for key in [k.strip() for k in args.models.split(",") if k.strip()]:
        print(f"Cross-validating {key} ({args.folds}-fold)...")
        name, summary, oof = cross_validate(df, key, args.folds)
        best_f1, at_recall, curve = sweep_thresholds(y, oof, args.target_recall)
        report[name] = {"cv_mean_std": summary, "best_f1_threshold": best_f1,
                        "target_recall_threshold": at_recall,
                        "oof_pr_auc": float(average_precision_score(y, oof))}
        curves[name] = curve
        print()

    winner = max(report, key=lambda n: report[n]["cv_mean_std"]["f1"][0])

    lines = ["SENTINEL - CROSS-VALIDATED EVALUATION", "=" * 44,
             f"{args.folds}-fold stratified CV | {len(df)} postings | {int(y.sum())} fraudulent ({y.mean()*100:.1f}%)",
             "Metrics are for the FRAUD class at the default 0.5 threshold; mean +- std over folds.", ""]
    for name, r in report.items():
        lines.append(name + ("   <-- best by CV F1" if name == winner else ""))
        lines.append("-" * len(name))
        for k, (m, s) in r["cv_mean_std"].items():
            lines.append(f"  {k:10s}: {m:.4f} +- {s:.4f}")
        b, t = r["best_f1_threshold"], r["target_recall_threshold"]
        lines.append(f"  Threshold for best F1            : {b['threshold']:.3f}  "
                     f"(P={b['precision']:.3f}, R={b['recall']:.3f}, F1={b['f1']:.3f})")
        lines.append(f"  Threshold for recall >= {args.target_recall:.2f}      : {t['threshold']:.3f}  "
                     f"(P={t['precision']:.3f}, R={t['recall']:.3f}, F1={t['f1']:.3f})")
        lines.append("")
    text = "\n".join(lines)
    print(text)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    with open(os.path.join(ASSETS_DIR, "cv_report.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(os.path.join(ASSETS_DIR, "cv_report.json"), "w", encoding="utf-8") as f:
        json.dump({"folds": args.folds, "target_recall": args.target_recall, "winner": winner,
                   "models": report}, f, indent=2)

    plt.figure(figsize=(6, 5))
    for name, (prec, rec, _) in curves.items():
        plt.plot(rec, prec, label=f"{name} (AP={report[name]['oof_pr_auc']:.3f})",
                 linewidth=2.4 if name == winner else 1.4)
        t = report[name]["target_recall_threshold"]
        plt.scatter([t["recall"]], [t["precision"]], zorder=5)
    plt.xlabel("Recall (scams caught)")
    plt.ylabel("Precision (alerts that are real scams)")
    plt.title(f"Precision-Recall, out-of-fold ({args.folds}-fold CV)")
    plt.xlim(0, 1.01); plt.ylim(0, 1.01); plt.legend(loc="lower left", fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, "pr_curve.png"), dpi=140)
    plt.close()

    if args.apply:
        info_path = os.path.join(MODELS_DIR, "model_info.json")
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
        live = info.get("best_model")
        key = "best_f1_threshold" if args.objective == "f1" else "target_recall_threshold"
        chosen = report.get(live, report[winner])[key]
        info["decision_threshold"] = round(chosen["threshold"], 4)
        info["cv"] = {"folds": args.folds, "model": live if live in report else winner,
                      "objective": args.objective, "target_recall": args.target_recall,
                      "metrics_at_threshold": {k: round(chosen[k], 4) for k in ("precision", "recall", "f1")},
                      "default_threshold_metrics": {k: round(v[0], 4) for k, v in
                                                    report.get(live, report[winner])["cv_mean_std"].items()}}
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        print(f"\nApplied decision_threshold={info['decision_threshold']} to {info_path}")
    else:
        print("\n(dry run - add --apply to store the threshold in models/model_info.json)")


if __name__ == "__main__":
    main()
