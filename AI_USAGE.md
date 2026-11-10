# AI Use and Decision Boundaries

FinGuard uses local AI only where text interpretation benefits from it. The application remains usable without a model.

## Where Ollama can act

- **Invoice extraction:** PyMuPDF or EasyOCR first produces source text. The rule-based parser extracts common fields. If key fields are missing and a configured local Ollama model is available, the approval pipeline asks it for a JSON object containing only fields supported by the text. A Pydantic schema validates the result; invalid or missing values remain empty. The run records `text+ollama` or `ocr+ollama` only when a model response was actually used. Any case with model-supplied fields is flagged for source review.
- **Evidence Q&A:** The existing evidence index retrieves invoice and bank records. A deterministic answer is built from those records. Ollama can optionally rewrite that cited answer without introducing new facts. The UI labels the answer mode.

The default model is `qwen2.5-coder:1.5b` at a local Ollama endpoint. Availability is checked at runtime. `FINGUARD_DISABLE_OLLAMA=1` forces deterministic fallback.

## Where AI does not act

- Vendor eligibility, PO status and balance, tolerance, tax and line arithmetic, duplicate classification, suggested approval status, bank matching, paid amount and outstanding balance are calculated by Python rules using `Decimal`.
- AI never posts a human review, marks an invoice paid, executes a payment or changes a PO.
- OCR is document text recognition, not a finance decision. If OCR or SLM extraction is uncertain, the invoice is routed to review with visible warnings.

## Why the architecture matters

A fluent answer can be wrong. FinGuard stores raw source text, the original PDF, extraction method, page references, validation checks, reason codes and an immutable system suggestion. A human can inspect and correct a record; the correction and final decision are stored separately. This makes model assistance useful without allowing it to silently determine a financial outcome.
