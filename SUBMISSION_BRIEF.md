# FinGuard — Written Brief Material (PS-1)

## 1. What we built, for whom, and why

FinGuard is a local AI-assisted **invoice lifecycle workspace** for an Accounts Payable (AP) analyst at a mid-sized company. It addresses two related but distinct questions. First, **should this vendor invoice be approved for payment?** Second, **was the approved invoice actually paid, and how much remains outstanding?** The PS-1 focus is the first question; the existing bank reconciliation capability remains available as the second stage.

The analyst can upload invoice PDFs, a vendor master and a purchase order (PO) register. FinGuard extracts document fields, identifies the vendor and PO, checks duplicates, amount, balance, tax and line items, then suggests Approved, Needs review or Rejected with visible evidence. The analyst can inspect the original PDF, correct fields, choose a PO, add a note and make the final decision. A separate bank CSV workflow matches payments to the same invoice records, including partial and unmatched payments. Both suggestions and human actions persist in SQLite with run history and CSV exports.

This matters because invoice intake and payment reconciliation often live in separate spreadsheets, mailboxes and ERP screens. FinGuard makes that handoff explicit and keeps an audit trail across it. It does not execute payments.

## 2. Area explored and what surprised us

We explored finance operations, specifically the path from a vendor PDF to an AP approval decision and then to a bank-backed payment status. The challenging part is not merely reading text from a PDF. An invoice can be machine-readable, scanned, bundled, missing a PO reference or inconsistent with the vendor master. Even when all text is extracted, approval requires policy and arithmetic: PO remaining balance, tolerance, tax treatment, duplicate identity and line-item evidence.

The most important product insight was that **approval and payment are independent states**. An invoice can be valid and approved but remain unpaid. A bank transaction can resemble an invoice that should not have been approved. A second valid invoice can reference the same PO without being a duplicate, while a repeated invoice number from the same vendor is a hard stop. These distinctions drove the data model, decision rules and UI navigation.

## 3. Problem statement and traditional process

**Who:** AP analysts and finance operations teams in mid-sized businesses that receive many vendor PDFs each month.

**Task and frequency:** Each invoice needs intake, field entry, vendor verification, PO lookup, amount/tax/line checks, duplicate checking, an approval decision and later payment tracking. The brief describes hundreds of invoices per month, so this is a daily operational task.

**What it costs today:** Traditionally an analyst opens PDFs, keys fields into a spreadsheet or ERP, searches the PO register, compares values manually, routes exceptions by email, and later reconciles bank statements separately. That uses analyst time, delays decisions, weakens traceability and creates risk of duplicate approval or mistaken payment attribution. For scale, **300 invoices × 8 minutes would equal 40 analyst hours per month before exception handling**; this is an illustrative assumption, not a measured result or a claimed saving.

**Simplification:** FinGuard performs the repetitive extraction and checks once, groups the evidence in one view, and routes only uncertain or policy-violating cases to a person. The human still owns the final exception decision.

## 4. Solution and features

The primary workflow accepts one or many PDFs. A ten-stage backend run records ingestion, PDF text or OCR, field extraction, validation, vendor verification, PO matching, duplicate search, financial checks, decision generation and persistence. The UI polls those actual stage events. The original PDF, extraction method, raw text, page references, checks and reason codes are available for inspection.

A deterministic engine selects an exact PO reference first and treats implied or ambiguous matches conservatively. It tracks cumulative approved invoices against a PO, so split invoices can be valid and over-consumption is flagged. Source hashes and vendor/invoice identity support duplicate detection. `Decimal` is used for money. The synthetic dataset contains eight numbered invoice PDFs, seven vendor records, eight POs, a connected bank CSV and a ninth held-out PDF. Three invoices are processed on startup; the rest and the bank CSV remain available for a live upload demonstration. Four deliberate edge cases show a scanned missing-number invoice, split invoices with an excess, a duplicate, and a tax discrepancy.

The payment workflow uses invoice references, vendor text, dates and amounts to suggest paid, partial, unpaid, needs-review and unmatched-bank outcomes. An explicit bank reference cannot be counted against another invoice from the same vendor. Analysts can correct payment matches or paid amounts separately from approval.

## 5. Architecture diagram instructions

Draw **one left-to-right lifecycle**, with a horizontal human lane above the technical flow.

- **Left / inputs:** Three document icons: invoice PDFs, vendor + PO CSVs, bank CSV. The AP analyst uploads or loads the fictional demo files.
- **Middle / ingestion:** A FastAPI box validates file type and size, assigns IDs, stores the original PDF bytes and SHA-256 fingerprint, and creates a processing run. A line from each invoice leads to **PyMuPDF text extraction**; an image-only branch leads to **optional EasyOCR**. Both feed **structured invoice extraction**, where rule-based parsing is the default and a dotted arrow from **local Ollama SLM** fills missing fields only when available. Attach a **Pydantic validation / normalization** box after extraction; missing fields remain null. Label the dotted AI arrow “interpret text, do not decide.”
- **Middle / approval decision:** Show a deterministic rule engine with sub-boxes for vendor eligibility, exact/implied PO matching, duplicate detection, remaining balance/tolerance, tax and line items. It outputs **Approved / Needs review / Rejected**, reason codes and source evidence. A bold boundary should show that the SLM has no arrow to the final decision. Use a `Decimal` calculator symbol for financial checks.
- **Lower connected branch / payment:** The same invoice record also feeds a separate **bank transaction matcher** along with bank CSV rows. It outputs **Paid / Partial / Unpaid / Needs review / Unmatched bank** and outstanding amount. There must be no arrow from Approved directly to Paid and no arrow from bank match to approval.
- **Right / persistence and views:** A central SQLite cylinder holds source documents, invoices, vendor/PO records, approval runs/events, machine suggestions, bank transactions, payment results, human reviews and evidence index. From it, arrows go to Overview, Live run, Invoice review, Payment reconciliation, History, Evidence Q&A and CSV reports. Counts and exports are read from this cylinder, not static UI arrays.
- **Top human lane:** The analyst inspects uncertain cases, corrects fields or selects a PO, approves/rejects, reviews bank matches, and writes notes. Human decisions return to separate append-only audit tables. Label the original machine suggestion as preserved.
- **Failure branch:** A malformed PDF goes to Failed run; unavailable OCR, incomplete extraction, ambiguous PO or shared bank candidate goes to Needs review. This makes uncertainty visible rather than silently fabricating a result.

The actual Mermaid version and data model are in [ARCHITECTURE.md](ARCHITECTURE.md).

## 6. What we added beyond AI

The value is the workflow and controls around AI: a real PDF upload path; document and source hashing; a synthetic yet connected vendor/PO/bank dataset; deterministic finance rules; cumulative PO consumption; distinct duplicate categories; explicit uncertainty states; a live run trace; source PDF and page evidence; human corrections with an audit trail; separate approval and payment decisions; local persistence; and reproducible exports. These are product and systems decisions. A text model alone cannot safely supply them.

The UI is structured around the analyst’s task: overview, invoice approval, payment reconciliation, processing history and reports. The model’s availability is optional rather than a hidden dependency. This makes the prototype explainable in an interview and runnable on a fresh local clone.

## 7. Technology choices and rejected alternatives

React, TypeScript, Vite and Tailwind CSS provide a responsive analyst interface. FastAPI and Pydantic keep the API and extraction schema explicit. PyMuPDF handles selectable PDF text; EasyOCR is an optional local scan path; Ollama is an optional local text interpreter. SQLite is sufficient for a single-user prototype and stores the evidence and decisions. Python `Decimal` keeps monetary checks exact. The generated documents use PyMuPDF and Pillow, avoiding a paid service or cloud dependency.

We rejected pure LLM approval because arithmetic and policy decisions must be reproducible. We rejected an automatic fuzzy PO/vendor match because it can approve the wrong invoice. We rejected a separate vector database, agents and microservices because the local evidence volume and assignment scope do not warrant them. We rejected precomputed demo statuses: the committed PDFs actually pass through the parser and rule engine.

## 8. Risks and controls

- **Wrong answers or extraction:** The raw PDF, extracted text, method, page references, validation checks and reason codes are visible. Missing or conflicting values route to review; the LLM does not decide status.
- **Outdated rules:** PO status, tolerance, vendor eligibility and tax rates are explicit synthetic register data and Python rules. Production use would require policy ownership, versioning and an authoritative ERP connection.
- **Confidential data:** The demo is fictional. Uploaded files stay in local SQLite; Ollama is local. There is no authentication or encryption at rest in this prototype, so it should not be publicly hosted or used with confidential production invoices.
- **Liability:** The product offers a recommendation and audit record, not payment execution, tax advice or a legal determination. Human review is required for exceptions. Before production, an organization would need approved policies, access control and legal/tax review.

## 9. What to validate before investing further

Measure extraction quality and false approvals on a permissioned, diverse invoice corpus, including poor scans and unusual line layouts. Test with actual AP analysts to learn which warnings are useful and which create review fatigue. Confirm real vendor/PO policy with procurement and finance owners. Evaluate ERP integration, authorization, data retention, backups, durable job processing, concurrency, monitoring, audit requirements and tax jurisdiction rules. Only then would it make sense to extend the prototype into a production AP system.
