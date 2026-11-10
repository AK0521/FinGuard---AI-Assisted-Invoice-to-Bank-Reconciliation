# FinGuard — Live Recording Guide

Start both services using [README.md](README.md). On a fresh database, three invoices are processed through the actual pipeline. If you have used FinGuard before, click **Reload demo workspace** on Overview. The confirmation should say that three invoices were restored; no bank CSV is imported. The in-app **Sample documents** view shows the current processed/waiting state from SQLite and the included sample files.

## Recommended recording sequence

| Step | Page and action | What to point out |
| --- | --- | --- |
| 1 | **Overview** → Reload demo workspace | Three real invoice runs are present. Approval and payment are separate. The bank statement is still waiting to be uploaded. |
| 2 | **Invoice approval** → Inspect `01_metro_clean.pdf`, then `02_kaveri_tax_discrepancy.pdf` | Metro passes vendor, PO, amount and tax checks. Kaveri needs review because INR 7,000 tax differs from the PO's 18% rate on INR 35,000. |
| 3 | **Sample documents** → inspect the file list | The guide shows the exact filenames, scenarios, current processed state and upload page. The files are in the repository's `sample_data` folder. |
| 4 | **Invoice approval** → upload `03_pixelcraft_bundled.pdf` | Choose the PDF with the existing file picker. Watch the ten real backend stages, then inspect its source PDF and suggested decision. |
| 5 | **Invoice approval** → upload `05_blue_lotus_split_two.pdf`, then `06_blue_lotus_over_balance.pdf` | Preloaded `04` consumed INR 12,000 of `PO-PACK-401`; `05` consumes another INR 12,000. `06` requests INR 10,000 with only INR 6,000 left, so it needs review. |
| 6 | **Payment reconciliation** → upload `bank_statement.csv` | The backend parses five rows. Metro and Blue Lotus `BLP-9001` are paid; Kaveri and `BLP-9002` are partial; the card purchase is unmatched. Other invoices remain unpaid. Approval decisions do not change. |
| 7 | **Invoice approval** → upload `07_metro_duplicate.pdf` | It repeats Metro invoice number `MFS-908`, which already exists from `01`. The approval engine rejects the duplicate. The payment engine may now flag the two Metro records as ambiguous rather than count one bank row twice. |
| 8 | **Invoice approval** → upload `08_cedar_scanned_missing_number.pdf` | This PDF is image-only. EasyOCR is attempted only if installed with local weights; otherwise the fallback is visible. The missing invoice number remains a review case. |
| 9 | **Invoice review**, **Processing history**, **Reports** | Add a human note/decision on a Needs review case, show the separate audit entry, previous runs and real CSV exports. Optionally upload `live_upload/09_blue_lotus_live_upload.pdf` to show another clean PO. |

After recording, **Reload demo workspace** restores the same three opening invoices, procurement reference data, zero bank rows and no human reviews. Repeating this reset does not accumulate records. Selecting the same bank CSV twice without resetting is also skipped by file fingerprint.

## Files kept for live upload

| File | Scenario |
| --- | --- |
| `03_pixelcraft_bundled.pdf` | New clean processing run with a different invoice layout. |
| `05_blue_lotus_split_two.pdf` | Valid second invoice against `PO-PACK-401`. |
| `06_blue_lotus_over_balance.pdf` | Exceeds the shared PO's remaining balance after `04` and `05`. |
| `07_metro_duplicate.pdf` | Duplicates the preloaded `01` invoice number. |
| `08_cedar_scanned_missing_number.pdf` | Image-only OCR/fallback and missing-number review. |
| `live_upload/09_blue_lotus_live_upload.pdf` | Optional clean invoice against separate `PO-PACK-402`. |
| `bank_statement.csv` | Full, partial and unmatched bank payments; upload on Payment reconciliation. |

Ollama is optional. It can fill missing fields from irregular text and help word cited Q&A answers, but never decides approval or payment. Any model-supplied invoice fields trigger source review. The demo works through the deterministic fallback when Ollama is unavailable.
