"""
Eval harness for Demo 1 (document intake).

It answers one question: how good was the last pipeline run?
It compares what the pipeline extracted (the "Entries" sheet, downloaded as CSV)
with the ground truth in test-data/expected_output_v2.json, then:

  1. prints a report (accuracy per field, routing, cost),
  2. writes one row per document x field to results/<run>-details.csv,
  3. appends one summary row to results/runs.csv, so runs can be compared over time.

Run it after every prompt or model change:

    python3 demo1-evals/evaluate.py --entries "Demo1 - Entries - Entries.csv"

Standard library only - nothing to install.
"""

import argparse
import csv
import json
import unicodedata
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent                      # the demo1-evals/ folder
REPO = HERE.parent                                # the repo root
DEFAULT_TRUTH = REPO / "test-data" / "expected_output_v2.json"
RESULTS_DIR = HERE / "results"

# Which fields to check for each document type.
# "Hard" fields have one right answer (numbers, dates, IDs). A wrong hard field
# is a real error in the books.
HARD_FIELDS = {
    "invoice":        ["vendor_name", "invoice_number", "invoice_date", "due_date",
                       "currency", "net_total", "vat_rate_percent", "vat_amount", "gross_total"],
    "utility_bill":   ["vendor_name", "invoice_number", "invoice_date", "due_date",
                       "currency", "net_total", "vat_rate_percent", "vat_amount", "gross_total"],
    "receipt":        ["vendor_name", "receipt_number", "date",
                       "currency", "net_total", "vat_rate_percent", "vat_amount", "gross_total"],
    "bank_statement": ["bank_name", "period_from", "period_to",
                       "opening_balance", "closing_balance", "transaction_count"],
}
# "Soft" fields are judgment calls (an accountant could reasonably pick another
# category). They are reported separately so they don't hide real errors.
SOFT_FIELDS = ["suggested_expense_category"]

# The broken Nordika invoice: the ground truth stores both what is PRINTED on the
# invoice and the correct value. Extraction should read what is printed
# (validation, not extraction, is supposed to catch the error).
TRUTH_ALIASES = {
    "vat_amount": "vat_amount_on_invoice",
    "gross_total": "gross_total_on_invoice",
}

MONEY_TOLERANCE = 0.01


# ---------- small helpers ----------

def normalize_text(value):
    """Lower-case, strip accents and extra spaces: 'CIRCLE D DEGALINĖ' -> 'circle d degaline'."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def to_number(value):
    """Turn '1105.94', 1105.94 or '' into a float (or None if empty)."""
    if value is None or value == "":
        return None
    return float(value)


def first_page(pages):
    """'10-11' -> 10, '3' -> 3, [10, 11] -> 10. Used to pair a row with its ground truth."""
    if isinstance(pages, list):
        return pages[0]
    return int(str(pages).split("-")[0])


# ---------- comparing one field ----------

def compare(field, expected, actual):
    """Return (is_match, note). Each kind of field has its own rule."""
    if expected is None and actual in (None, ""):
        return True, "both empty"

    if field in ("net_total", "vat_amount", "gross_total", "vat_rate_percent",
                 "opening_balance", "closing_balance", "transaction_count"):
        exp, act = to_number(expected), to_number(actual)
        if exp is None or act is None:
            return False, "missing number"
        return abs(exp - act) <= MONEY_TOLERANCE, ""

    if field in ("vendor_name", "bank_name"):
        # Names: accept small differences, e.g. the truth has a legal name in brackets.
        exp, act = normalize_text(expected), normalize_text(actual)
        if exp == act:
            return True, ""
        if act and (act in exp or exp in act):
            return True, "partial name match"
        return False, ""

    # IDs, dates, currency, category: exact after normalizing case and spaces.
    return normalize_text(expected) == normalize_text(actual), ""


# ---------- loading the data ----------

def load_truth(path):
    """Ground truth documents, keyed by their first page."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    truth = {}
    for doc in data["documents"]:
        doc = dict(doc)
        doc["expected_status"] = ("needs_review"
                                  if doc.get("validation", "").startswith("NEEDS_REVIEW")
                                  else "pass")
        truth[first_page(doc["pages"])] = doc
    return truth


def load_run(entries_csv, run_id=None):
    """Rows of one run from the Entries sheet export. Defaults to the latest run."""
    with open(entries_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows in {entries_csv}")

    run_ids = sorted({row["run_id"] for row in rows})
    chosen = run_id or run_ids[-1]
    run_rows = [row for row in rows if row["run_id"] == chosen]
    if not run_rows:
        raise SystemExit(f"Run '{chosen}' not found. Runs in the file: {run_ids}")

    for row in run_rows:
        # The sheet stores the full model output as a JSON string in 'extracted'.
        row["extracted"] = json.loads(row.get("extracted") or "{}")
        if "transactions" in row["extracted"]:
            row["extracted"]["transaction_count"] = len(row["extracted"]["transactions"])
    return chosen, run_rows


def run_cost(rows):
    """Classification cost is paid once per pack; extraction once per document."""
    pack = json.loads(rows[0].get("_pack") or "{}")
    extract = sum(json.loads(r.get("_extract") or "{}").get("cost_usd", 0) for r in rows)
    return round(pack.get("cost_usd", 0) + extract, 4)


# ---------- the evaluation ----------

def evaluate(truth, rows):
    details = []          # one dict per document x field
    routing = []          # one dict per document: expected vs actual status
    matched_pages = set()

    for row in rows:
        page = first_page(row["page_range"])
        expected = truth.get(page)
        if expected is None:
            routing.append({"pages": row["page_range"], "problem": "no ground truth for this page"})
            continue
        matched_pages.add(page)
        extracted = row["extracted"]
        doc_type = expected["doc_type"]
        label = expected.get("vendor_name") or expected.get("bank_name")

        # Document type is checked for every document.
        ok, _ = compare("doc_type", doc_type, row["doc_type"])
        details.append({"pages": row["page_range"], "document": label, "field": "doc_type",
                        "kind": "hard", "expected": doc_type, "actual": row["doc_type"],
                        "match": ok, "note": ""})

        for kind, fields in (("hard", HARD_FIELDS.get(doc_type, [])), ("soft", SOFT_FIELDS)):
            for field in fields:
                if field not in expected and TRUTH_ALIASES.get(field) not in expected:
                    continue   # the truth doesn't define this field for this document
                exp_value = expected.get(field, expected.get(TRUTH_ALIASES.get(field)))
                act_value = extracted.get(field)
                ok, note = compare(field, exp_value, act_value)
                details.append({"pages": row["page_range"], "document": label, "field": field,
                                "kind": kind, "expected": exp_value, "actual": act_value,
                                "match": ok, "note": note})

        routing.append({"pages": row["page_range"], "document": label,
                        "expected": expected["expected_status"], "actual": row["status"]})

    missing = [doc for page, doc in truth.items() if page not in matched_pages]
    return details, routing, missing


def summarize(run_id, details, routing, missing, cost):
    hard = [d for d in details if d["kind"] == "hard"]
    soft = [d for d in details if d["kind"] == "soft"]
    routed = [r for r in routing if "expected" in r]
    return {
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "documents": len(routed),
        "documents_missing": len(missing),
        "hard_fields_checked": len(hard),
        "hard_field_accuracy": round(sum(d["match"] for d in hard) / len(hard), 4) if hard else None,
        "soft_field_accuracy": round(sum(d["match"] for d in soft) / len(soft), 4) if soft else None,
        "routing_correct": sum(r["expected"] == r["actual"] for r in routed),
        # The dangerous error: a document that should go to a human was auto-filed.
        "false_pass": sum(r["expected"] == "needs_review" and r["actual"] == "pass" for r in routed),
        # The annoying error: a clean document was sent to review.
        "false_review": sum(r["expected"] == "pass" and r["actual"] == "needs_review" for r in routed),
        "cost_usd": cost,
    }


# ---------- output ----------

def print_report(summary, details, routing, missing):
    print(f"\nDemo 1 eval - run {summary['run_id']}")
    print("=" * 60)
    print(f"Documents evaluated:  {summary['documents']}  (missing: {summary['documents_missing']})")
    print(f"Hard-field accuracy:  {summary['hard_field_accuracy']:.1%}  "
          f"({summary['hard_fields_checked']} fields)")
    if summary["soft_field_accuracy"] is not None:
        print(f"Category accuracy:    {summary['soft_field_accuracy']:.1%}  (judgment field)")
    print(f"Routing correct:      {summary['routing_correct']}/{summary['documents']}  "
          f"false pass: {summary['false_pass']}  false review: {summary['false_review']}")
    print(f"API cost:             ${summary['cost_usd']}")

    print("\nAccuracy per field")
    per_field = {}
    for d in details:
        per_field.setdefault(d["field"], []).append(d["match"])
    for field, results in per_field.items():
        print(f"  {field:<28} {sum(results)}/{len(results)}")

    mismatches = [d for d in details if not d["match"]]
    print("\nMismatches" if mismatches else "\nMismatches: none")
    for d in mismatches:
        print(f"  p.{d['pages']:<6} {d['document'][:28]:<28} {d['field']:<26} "
              f"expected {d['expected']!r}  got {d['actual']!r}  [{d['kind']}]")

    for r in routing:
        if "problem" in r:
            print(f"  p.{r['pages']}: {r['problem']}")
        elif r["expected"] != r["actual"]:
            kind = "FALSE PASS" if r["actual"] == "pass" else "false review"
            print(f"  p.{r['pages']:<6} {r['document'][:28]:<28} routing: expected {r['expected']}, "
                  f"got {r['actual']}  [{kind}]")
    for doc in missing:
        print(f"  MISSING: pages {doc['pages']} ({doc.get('vendor_name') or doc.get('bank_name')})")
    print()


def save_results(summary, details):
    RESULTS_DIR.mkdir(exist_ok=True)
    safe_run = summary["run_id"].replace(":", "").replace(" ", "_")

    details_path = RESULTS_DIR / f"{safe_run}-details.csv"
    with open(details_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(details[0].keys()))
        writer.writeheader()
        writer.writerows(details)

    runs_path = RESULTS_DIR / "runs.csv"
    is_new = not runs_path.exists()
    with open(runs_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(summary)
    return details_path, runs_path


def main():
    parser = argparse.ArgumentParser(description="Score a Demo 1 run against the ground truth.")
    parser.add_argument("--entries", required=True, help="CSV download of the Entries sheet")
    parser.add_argument("--truth", default=DEFAULT_TRUTH, help="ground-truth JSON")
    parser.add_argument("--run-id", help="which run to score (default: the latest)")
    parser.add_argument("--no-save", action="store_true", help="print only, don't write results/")
    args = parser.parse_args()

    truth = load_truth(args.truth)
    run_id, rows = load_run(args.entries, args.run_id)
    details, routing, missing = evaluate(truth, rows)
    summary = summarize(run_id, details, routing, missing, run_cost(rows))
    print_report(summary, details, routing, missing)

    if not args.no_save:
        details_path, runs_path = save_results(summary, details)
        print(f"Saved {details_path.relative_to(REPO)} and appended to {runs_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
