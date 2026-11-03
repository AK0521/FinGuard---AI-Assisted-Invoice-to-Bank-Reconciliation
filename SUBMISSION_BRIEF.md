# FinGuard Submission Brief

## What We Built

FinGuard is a local invoice-to-bank reconciliation workspace for small-business finance teams. It helps a bookkeeper, accountant, founder, or finance operations analyst compare vendor invoice PDFs against outgoing bank transactions, inspect evidence, ask questions over the evidence, and save final human review decisions.

The prototype is not a chatbot that guesses accounting outcomes. It is a structured workflow: parse documents, normalize records, run deterministic reconciliation, show evidence, let the human approve or correct, and export the result.

## Area Explored And What Surprised Us

We explored finance operations, specifically vendor invoice reconciliation. The surprising part was that the most useful AI design was not "ask an LLM if this invoice is paid." That would be unsafe. The useful design was separating responsibilities:

- deterministic code decides suggested payment status and amounts,
- OCR/parser handles document extraction,
- local SLM helps with unstructured invoice text and evidence Q&A,
- the human analyst makes or confirms final review decisions.

Another learning was that evaluator experience matters. A blank prototype looks unfinished even if upload works, so FinGuard now loads a bundled sample evaluation workspace and includes matching verifier files in `test_uploads_2/`.

## Problem Statement

Small-business finance teams often reconcile invoices and bank payments manually. The user may be a bookkeeper, accountant, founder, or operations analyst. The task is to answer: which invoices are paid, partially paid, unpaid, ambiguous, or unsupported by bank evidence?

This happens weekly or monthly, and sometimes daily for businesses with frequent vendor payments. Today it is commonly solved with PDF invoices, downloaded bank CSVs, spreadsheets, search, VLOOKUP/XLOOKUP, and manual notes. The cost is time, delayed month-end close, missed partial payments, duplicate payments, and weak audit traceability.

FinGuard simplifies the work by turning PDFs and CSVs into one reviewable workspace with suggested matches, evidence, status explanations, human review controls, and export.

## Target User

The primary user is a finance analyst or bookkeeper at a small or mid-sized business. Secondary users include founders managing payments, accountants preparing close, and evaluators/auditors reviewing payment evidence.

The user needs speed, but also needs trust. A wrong "paid" answer can create real financial risk, so the product must be explainable and correctable.

## Main Workflow

1. The evaluator opens the app and sees a sample workspace immediately.
2. The user can upload invoice PDFs and one or more bank CSV statements.
3. FinGuard extracts invoice fields and bank rows.
4. The deterministic engine suggests reconciliation status.
5. The workspace shows status, matched amount, outstanding amount, and reason.
6. The user clicks Inspect to review invoice evidence, bank candidates, flags, and audit trail.
7. The user approves, rejects, corrects a match, or edits paid amount.
8. The user can ask Evidence Q&A questions with citations.
9. The user exports a CSV report.

## What We Added Beyond AI

The project value is not just the SLM. We added deterministic reconciliation, a human review workflow, evidence detail, audit trail, local persistence, CSV export, default evaluator dataset, and a professional dashboard UI.

The AI is deliberately bounded. It helps convert unusual invoice text into structured fields and helps answer cited evidence questions. It does not decide final truth alone.

## Solution Design

Architecture diagram guidance:

- Left side: Human analyst/evaluator.
- Next: React workspace where the human uploads files, views rows, inspects evidence, asks Q&A, and saves review decisions.
- Backend: FastAPI.
- Ingestion branch 1: invoice PDFs go through PyMuPDF text extraction. If no selectable text exists, optional EasyOCR performs OCR. The parser extracts vendor, invoice number, date, tax, total, and raw text. If fields are missing, optional local Ollama performs strict JSON extraction from extracted text.
- Ingestion branch 2: bank CSVs are parsed into date, description, debit/credit, reference, and row number.
- Storage: SQLite stores source files, invoices, bank transactions, reconciliation results, review decisions, and evidence index.
- Decision engine: deterministic reconciliation uses reference match, vendor token match, date window, amount exact/partial/mismatch logic, and ambiguity detection.
- Evidence layer: records are indexed for retrieval; Evidence Q&A retrieves citations and optionally sends grounded answer text to Ollama for clearer wording.
- Human review: the analyst approves, rejects, corrects, selects a bank candidate, edits paid amount, and leaves notes.
- Export: CSV report includes suggested outcome, final status, amounts, reasons, and review notes.

Key visual distinction: deterministic engine owns suggested status; local AI owns extraction help and language help; human owns final review.

## Technology Stack

- React, TypeScript, Vite, Tailwind CSS for the frontend workspace.
- FastAPI and Pydantic for backend API and typed request/response models.
- SQLite for simple local persistence.
- PyMuPDF for PDF text extraction.
- EasyOCR as optional scanned-PDF OCR.
- Ollama with `qwen2.5-coder:1.5b` as optional local SLM.
- Pytest for backend tests.

These choices keep the submission runnable locally without cloud credentials or a managed database.

## Alternatives Rejected

- Pure LLM reconciliation: rejected because financial status should not depend on probabilistic generation.
- Cloud LLM APIs: rejected for local privacy and no-key setup.
- PostgreSQL/vector database/Docker: rejected to reduce evaluator setup complexity.
- Fully automated approval: rejected because finance workflows need accountability.
- Spreadsheet-only approach: rejected because it does not solve messy PDFs, evidence traceability, or human review history.

## Risks And Mitigations

Wrong answers: deterministic reconciliation, evidence display, conservative review status, and human approval reduce this risk.

Outdated rules: FinGuard does not provide tax/GST/legal advice. It reconciles operational payment evidence only.

Confidential data: the app runs locally, stores data in SQLite, and uses local Ollama if enabled. No cloud LLM key is required.

Liability: the system is positioned as analyst assist, not autonomous accounting authority. The human can override status and paid amount, and decisions are auditable.

OCR/parser errors: extracted fields are visible; missing or ambiguous fields are flagged; scanned PDFs depend on local OCR quality.

Hallucinated AI: Evidence Q&A is grounded in retrieved citations, and generative output does not mutate records.

## Things To Consider Before Spending More Time

Before expanding the project, validate how often target users reconcile invoices, which bank formats they use, how much accuracy they require, and whether integrations matter more than uploads. The next useful product steps would be multi-workspace history, authentication, accounting-system integrations, bank PDF parsing, stronger OCR validation, and better learning from analyst corrections.
