# FinGuard

FinGuard is a local, human-in-the-loop prototype for reconciling vendor invoice PDFs against outgoing bank statement transactions. It is designed for small-business finance operators, bookkeepers, accountants, and evaluators who need to see which invoices are paid, partially paid, unpaid, ambiguous, or unmatched against bank activity.

The project intentionally keeps the core reconciliation deterministic and reviewable. Local AI is used only for bounded assistance: extracting missing fields from already-extracted invoice text and rewriting cited evidence answers when Ollama is available.

## What It Does

- Loads a bundled sample evaluation workspace on first backend startup.
- Accepts invoice PDFs and one or more bank CSV statements.
- Extracts text from normal PDFs with PyMuPDF.
- Optionally runs OCR on scanned PDFs when EasyOCR is installed.
- Uses local Ollama SLM extraction for unusual invoice text layouts when available.
- Reconciles invoices to bank transactions using invoice references, vendor tokens, date windows, and amounts.
- Classifies records as Paid, Partial, Unpaid, Needs review, or Unmatched bank.
- Shows evidence detail with invoice data, bank candidates, flags, and natural-language reasons.
- Lets a human approve, reject, correct matches, edit paid amount, and preserve an audit trail.
- Provides cited Evidence Q&A using local retrieval, with optional Ollama-assisted wording.
- Exports a CSV report with suggested status, final status, amounts, evidence, and review notes.

## Repository Layout

```text
backend/              FastAPI API, parsing, reconciliation, retrieval, tests
frontend/             React/Vite/Tailwind analyst workspace
scripts/              Local scripts to regenerate sample upload packs
test_uploads_2/       Submission sample PDFs and bank CSVs for evaluator verification
ARCHITECTURE.md       System design and architecture diagram
SUBMISSION_BRIEF.md   Written brief material for the assignment
EVALUATION.md         Test and verification notes
AI_USAGE.md           Notes on local AI boundaries
```

## Architecture And Stack

- Frontend: React, TypeScript, Vite, Tailwind CSS, lucide-react icons.
- Backend: FastAPI, Pydantic, Python standard CSV utilities.
- Persistence: SQLite local database created at `data/finguard.sqlite3` by default.
- PDF extraction: PyMuPDF.
- Optional OCR: EasyOCR.
- Optional local SLM: Ollama with `qwen2.5-coder:1.5b`.
- Tests: pytest.

High-level flow:

```text
Human analyst
  -> React workspace
  -> FastAPI backend
  -> PDF/CSV ingestion
  -> PyMuPDF text extraction or optional EasyOCR
  -> parser plus optional local Ollama field extraction
  -> SQLite records
  -> deterministic reconciliation engine
  -> evidence index and cited Q&A
  -> human review decision and CSV export
```

See `ARCHITECTURE.md` for the full diagram and AI/human responsibility boundary.

## Prerequisites

- Python 3.11+ or 3.12
- Node.js 20+ recommended
- npm
- Optional: Ollama running locally with `qwen2.5-coder:1.5b`
- Optional: EasyOCR dependencies for scanned PDF OCR

No cloud LLM API key is required.

## Quick Start: Run The App

From a fresh clone, open two terminals from the repository root.

Terminal 1 - backend API:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2 - frontend UI:

```powershell
cd frontend
npm install
npm run dev
```

Then open the Vite URL shown in Terminal 2, usually:

```text
http://127.0.0.1:5173
```

On first backend startup, FinGuard automatically loads the included sample evaluation workspace, so the workspace should already show records without requiring evaluator uploads.

## Fresh Clone Setup

From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional OCR support for scanned PDFs:

```powershell
python -m pip install -r requirements-ocr.txt
```

In a second terminal, install frontend dependencies:

```powershell
cd frontend
npm install
```

## Run Locally

Start the backend:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```powershell
cd frontend
npm run dev
```

Open the Vite URL shown in the terminal, usually:

```text
http://127.0.0.1:5173
```

When the backend database is empty, FinGuard automatically seeds the bundled sample evaluation workspace. The UI should therefore show records immediately after a fresh run.

## Sample Workspace And Test Uploads

The app includes a sample evaluation workspace in backend seed data. To reset the UI back to that included workspace, click **Reload sample** in the left overview panel or call:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/demo
```

The matching sample files are included in `test_uploads_2/`:

- `01_metro_facility_services.pdf`
- `02_kaveri_paper_mart.pdf`
- `03_pixelcraft_design_studio.pdf`
- `04_blue_lotus_packaging_unstructured.pdf`
- `05_cedar_cleaning_care_scanned.pdf`
- `bank_statement_january.csv`
- `bank_statement_february.csv`

To reproduce the sample through the upload flow, upload all five PDFs as invoice PDFs and both CSV files as bank CSVs. The scanned Cedar invoice requires optional EasyOCR support to parse from the upload path; the default seeded workspace already contains the expected extracted record for evaluator convenience.

Expected sample status mix:

- 2 Paid invoices
- 1 Partial invoice
- 1 Unpaid invoice
- 1 Needs review invoice
- 1 Unmatched bank transaction

## Optional Ollama Setup

FinGuard works without Ollama. With Ollama available, Evidence Q&A returns `answer_mode: ollama_rag`, and unstructured invoice text can be converted into structured invoice fields.

Example:

```powershell
ollama pull qwen2.5-coder:1.5b
ollama serve
```

Optional environment variables are listed in `.env.example`.

## Run Tests

Backend tests:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q -p no:cacheprovider
```

Frontend production build:

```powershell
cd frontend
npm run build
```

## Known Limitations

- This is a local prototype, not production accounting software.
- There is one active local workspace; uploading files replaces the current workspace.
- No authentication or multi-user workspace isolation is implemented.
- Bank upload supports CSV, not scanned bank statement PDFs.
- OCR quality depends on the local EasyOCR installation and scan quality.
- Very unusual invoice layouts may need parser or SLM prompt additions.
- Matching is conservative and can require human review for ambiguous candidates.
- The app does not make payments, file taxes, validate GST compliance, or provide legal, tax, GST, or banking advice.

## Submission Notes

- Local database files are ignored by git.
- Sample verification files are intentionally included in `test_uploads_2/`.
- Build outputs and dependency folders are ignored.
- Keep secrets in local environment variables; do not commit `.env` files.
