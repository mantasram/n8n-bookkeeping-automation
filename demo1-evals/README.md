# Demo 1 evals

A small Python harness that scores a Demo 1 run against the ground truth, so every prompt or model change is measured instead of eyeballed.

It reads the pipeline's output (the `Entries` Google Sheet, downloaded as CSV), matches each row to `test-data/expected_output_v2.json` by page, and reports:

- **Hard-field accuracy**: vendor, invoice number, dates, currency, net, VAT rate, VAT amount, gross (and balances for the bank statement). These have one right answer.
- **Category accuracy**: the suggested expense category, reported separately because it's a judgment call.
- **Routing**: did each document end up where it should (`pass` vs `needs_review`)? A **false pass** is when a document that needed a human was filed automatically. That's the error that matters most.
- **Cost**: API spend for the run, from the token counts the workflow logs.

Each run writes `results/<run>-details.csv` (one line per document and field) and appends a summary line to `results/runs.csv`, so runs can be compared over time.

## Run it

Standard library only, Python 3.9+.

1. Run Demo 1 on `test-data/mixed_pack_intl_v2.pdf`.
2. In the `Entries` sheet: File → Download → Comma-separated values.
3. From the repo root:

```
python3 demo1-evals/evaluate.py --entries ~/Downloads/"Demo1 - Entries - Entries.csv"
```

It scores the latest run in the file by default. `--run-id "2026-09-22 8:28:36"` picks another one, and `--no-save` prints without writing results.

To try it without n8n, use the sample: `python3 demo1-evals/evaluate.py --entries demo1-evals/sample/entries-sample.csv --no-save`. The sample is rebuilt from the sheet rows of a real run on 22 Sep 2026, with line items and bank transactions trimmed.

## Rules worth knowing

- Money and rates match within 0.01.
- Names match after lower-casing and removing accents; a shorter name contained in the full legal name counts as a match (flagged as "partial").
- For the deliberately broken Nordika invoice, extraction is scored against what's printed on the invoice (VAT 24.72). Catching the error is validation's job, and that's checked through routing.
