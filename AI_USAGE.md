# AI Usage

This project uses AI in two different ways: AI-assisted development during the build process, and optional local AI inside the running application.

## AI During Development

AI assistance was used to help plan the product flow, draft code, refine the UI, write documentation, and identify edge cases. All critical product behavior was verified with local tests and manual API checks before being treated as working.

Important non-AI design decisions included:

- keeping reconciliation status deterministic rather than LLM-decided,
- preserving human review and audit trail,
- running without cloud LLM credentials,
- including a default sample workspace for evaluators,
- keeping uploaded workspaces local in SQLite.

## AI Inside FinGuard

FinGuard does not require a cloud LLM API key. If local Ollama is available, the app uses it only for bounded assistance:

- extracting missing invoice fields from text already extracted from a PDF,
- rewriting retrieved Evidence Q&A answers into clearer language.

The LLM is not allowed to approve records, create citations, calculate authoritative totals, or change final review decisions.

## Verification

The backend test suite covers parsing, reconciliation behavior, review persistence, cited Q&A behavior, and CSV export. The frontend production build was also run before submission-readiness review.
