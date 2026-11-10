# Evaluation — 10 November 2026

## Automated checks actually run

From `backend`:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q -p no:cacheprovider
```

Result after the recording-mode changes: **43 passed, 14 warnings in 192.04 seconds**. The warnings were Pydantic/FastAPI and EasyOCR/Torch deprecations; there were no failed assertions. The suite ran alongside local UI services, which made it slower than the earlier isolated run. The pytest cache provider was disabled because it previously hung at shutdown in this Windows environment.

From `frontend`:

```powershell
npm run build
```

Result after the final Sample documents UI edit: **passed**. TypeScript checked without errors; Vite built 1,582 modules in 14.12 seconds. On this machine the ignored `dist` directory required permission to replace because of a Windows file lock.

`git diff --check` exited successfully. A repository text scan found no hardcoded credentials, API keys or machine-specific Windows paths. The local SQLite workspace and build outputs are ignored by Git.

## Functional cases covered by the suite

- Exact, within-tolerance and over-tolerance invoice-to-PO decisions.
- Unapproved vendor, closed PO, missing or ambiguous PO, duplicate invoice and repeated source PDF.
- Valid split invoices, cumulative PO balance and over-consumption.
- Tax mismatch, missing invoice number, invalid date/amount, mismatched line descriptions, negative values and itemized line totals.
- Fresh startup processing only the three selected PDFs, with seven vendors, eight POs, zero bank transactions and matching dashboard counts.
- Live upload of all remaining sample PDFs, including the valid second split invoice, PO over-balance case, confirmed duplicate, image-only OCR/fallback case and held-out clean invoice.
- Live import of the bank CSV after the relevant invoices, full/partial/unmatched payment outcomes, linked payment detail without changing approval, and exact-file duplicate import prevention.
- Two consecutive demo resets restoring the same three invoices and clearing later uploads, bank rows and human approval reviews.
- Upload API, failed malformed PDF, OCR-unavailable review case, ten persisted stage events, source PDF and preview retrieval, human approval audit, CSV import and export.
- Payment exact/partial/unpaid/unmatched cases, date/amount ambiguity, citation references, payment review persistence and prevention of cross-invoice double counting.

## Local application smoke check

With a fresh local SQLite database, the browser displayed **three** opening invoices: two Approved and one Needs review. The in-app Sample documents guide showed those three as processed and the other PDFs and bank CSV as waiting. The browser's Payment reconciliation file picker imported `bank_statement.csv`; the page then showed two Paid invoices, one Partial invoice and an unmatched bank record. Selecting the same CSV again produced “already imported; duplicate CSV skipped” and did not change those counts. Overview refreshed its outstanding amount. Clicking Reload demo workspace displayed a confirmation and restored the original three unpaid invoices; the guide again showed the bank file as waiting. In an earlier isolated smoke check, the held-out `09` PDF also completed ten stages and rendered its source preview.

The recording-mode browser check used `FINGUARD_DISABLE_OLLAMA=1` to verify the fallback path. Separately, the local Ollama service previously reported `qwen2.5-coder:1.5b` available. A real irregular-PDF upload went through the API pipeline and stored `text+ollama` extraction, correctly separated the vendor name from the surrounding sentence, filled the missing date and total, and returned Needs review with `model_extraction_review`. Evidence Q&A returned `answer_mode=ollama_rag` with a cited bank answer. The automated approval rules do not depend on the model.

## Verification boundary

These checks ran on the current local checkout with installed dependencies. They do not prove all third-party invoice layouts, bank CSV schemas, low-quality scans or operating systems will work unchanged. OCR requires locally available EasyOCR model weights. This single-user prototype has no authentication, durable job queue, production policy source or real payment execution.
