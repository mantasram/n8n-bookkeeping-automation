# n8n bookkeeping automation — three working demos

Three end-to-end n8n workflows for the document-heavy back office of a small accounting or bookkeeping firm. Built on self-hosted n8n with the Claude API, with a hard rule: **nothing produced by an LLM lands in the books unchecked** — every AI output goes through a deterministic validation step and a human-review path.

| # | Workflow | What it does | AI in the loop? |
|---|---|---|---|
| 1 | [Document intake](workflows/demo1-document-intake.json) | One mixed client PDF pack in → classified, extracted, arithmetically validated bookkeeping entries out, files renamed and filed, summary email | Yes — classification + extraction, then a pure-arithmetic check with no AI |
| 2 | [Document chase](workflows/demo2-document-chase.json) | Daily reminder sequence for missing client documents, client upload form, Monday owner summary | **No** — templates only, so a reminder can never invent a deadline |
| 3 | [Firm assistant](workflows/demo3-firm-agent.json) | Chat agent that answers questions over the firm's data using read-only tools | Yes — agent with 4 read-only tools; never writes |

Demo videos: _coming soon_ — links will be added here. Author: [Mantas Ramoška](https://github.com/mantasram).

---

## 1. Document intake — "one messy PDF in, sorted books out"

**Input:** the test pack [`test-data/mixed_pack_intl_v2.pdf`](test-data/mixed_pack_intl_v2.pdf) — 14 pages, 12 documents: 8 invoices from 5 countries, a bank statement, a utility bill and 2 phone-scanned till receipts. Languages: LT, DE, SV, NL, EN. Currencies: EUR, SEK, USD. One invoice is deliberately broken.

**Pipeline:** `TriggerFormUpload → PreparePDF → ClassifyPack (Claude) → ParseClassification → SplitPDF (pdf-lib) → ExtractFields (Claude, per document) → ParseExtraction → Validate (no AI) → FileDocuments → AppendSheet → Summary → WriteSummary → SendSummaryEmail`

**Result on the test pack:** 12 rows written (vendor, number, date, net, VAT, gross, currency, category). 11 pass validation. 1 is routed to `needs-review/` — the VAT amount does not equal net × rate, *and* the rate is not a valid rate for that country. The AI read the numbers correctly; arithmetic caught the error. Ground truth is in [`test-data/expected_output_v2.json`](test-data/expected_output_v2.json).

**Cost:** roughly $0.20 of API spend per 14-page pack with Claude Sonnet for both steps. Not optimised — text-first classification, a cheaper model for simple documents and prompt caching would bring this to a few cents.

**Design choices worth noting**
- Amounts are never silently converted. SEK stays SEK, USD stays USD.
- Files are renamed `YYYY-MM-DD_vendor_number.pdf` and filed under `sorted/<type>/`; the flagged one goes to `needs-review/` with the reason.
- Validation is a plain Code node: totals, VAT arithmetic, country-rate table, date sanity. It has no access to the model.

## 2. Document chase — "chasing documents without the inbox tennis"

Reads a client list (Google Sheet), decides who needs which message, sends it, logs it. Four rules, all enforced in `ComputeChase` (a Code node):

1. Never email a client whose documents are complete.
2. Never send the same reminder twice (run it twice on the same day → second run sends nothing).
3. Three touches maximum (request → reminder 1 → reminder 2 with a real deadline), then stop chasing the client and escalate to the accountant.
4. Everything is logged (`AppendLog`).

Clients upload through an n8n form (`TriggerClientUpload`); the record updates itself and the client gets a thank-you in their language listing what is still missing. Every Monday the owner gets a table: who owes what, for how long, who needs a phone call.

Test client list: [`test-data/clients.csv`](test-data/clients.csv) (five synthetic clients, each designed to trigger one rule).

## 3. Firm assistant — "ask the firm's data"

An n8n AI Agent (Claude Sonnet, window memory) with four **read-only** tools: the intake entries sheet, the client list, the chase log and a Code tool with Lithuanian filing deadlines. Example questions it answers by reading the data, not guessing:

- *Which July documents need review, and why?*
- *Did Kavos Namai send their bank statement yet, and what's still missing?*
- *Kada artimiausias PVM terminas ir kas iš klientų dar neatsiuntė dokumentų?* (LT in → LT out, two tools in one answer)

In production the tools would read the firm's accounting system instead of Google Sheets. The agent is never given a write tool.

---

## Running it yourself

**Requirements:** n8n ≥ 1.100 self-hosted (tested on 2.x in Docker), an Anthropic API key, a Google Cloud project with Sheets + Gmail OAuth credentials.

1. Import the three JSON files from `workflows/` (n8n → Workflows → Import from file).
2. Create credentials in n8n: Anthropic, Google Sheets OAuth2, Gmail OAuth2. Attach them to the `ClassifyPack`/`ExtractFields` HTTP nodes (Demo 1), the `ClaudeModel` node (Demo 3), and every Google Sheets / Gmail node.
3. Create three Google Sheets and put their IDs into each workflow's `Config` node (`YOUR_ENTRIES_SHEET_ID`, `YOUR_CLIENTS_SHEET_ID`, `YOUR_CHASELOG_SHEET_ID`). Tab names: `Entries`, `Clients`, `ChaseLog`. Seed `Clients` from `test-data/clients.csv`.
4. Replace `you@example.com` in the `Config` nodes with your own address, and `https://YOUR-N8N-HOST` with your n8n public URL (needed for the upload form link in Demo 2).
5. Demo 1 needs `pdf-lib` available to Code nodes and a writable folder for filed documents. Container env:
   ```
   NODE_FUNCTION_ALLOW_EXTERNAL=pdf-lib
   NODE_FUNCTION_ALLOW_BUILTIN=fs,path
   NODE_PATH=/home/node/.n8n/custom-modules/node_modules
   ```
   with `pdf-lib` installed into `/home/node/.n8n/custom-modules` and a host folder mounted at `/files`.
6. Run Demo 1 with the test pack, then Demo 2, then open the Demo 3 chat — the agent answers over whatever the first two produced.

## Data protection

Built for EU clients: self-hosted n8n on EU infrastructure, only the document text needed for classification/extraction goes to the LLM API, no client data retained by the model provider beyond the request. The test data in this repo is entirely synthetic (`.example` domains, invented companies).

## License

MIT — see [LICENSE](LICENSE).
