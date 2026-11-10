"""Actual per-document processing and persisted stage telemetry."""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal

import fitz
from pydantic import ValidationError

from .approval import InvoiceFields, LineItem, PurchaseOrder, Vendor, evaluate_invoice, extract_fields, normalized, normalized_name
from .approval_store import (
    event, previous_invoices, purchase_orders, save_approval, save_source,
    set_source_status, update_run, upsert_procurement, vendors,
)
from .database import insert_invoices, new_id, run_reconciliation_and_store
from .llm import extract_invoice_fields_with_ollama, ollama_status
from .models import InvoiceRecord
from .parsing import parse_invoice_pdf


STAGES = [
    "document_received", "text_extraction", "field_extraction", "data_validation",
    "vendor_verification", "po_matching", "duplicate_detection",
    "amount_tax_validation", "decision_generation", "result_persisted",
]
MAX_PDF_BYTES = 12 * 1024 * 1024


def process_pdf(run_id: str, filename: str, contents: bytes) -> None:
    source_id: str | None = None
    invoice_id: str | None = None
    stage = STAGES[0]
    try:
        update_run(run_id, "running")
        event(run_id, stage, "running", "Validating and storing the original invoice PDF.")
        if not filename.lower().endswith(".pdf") or not contents.startswith(b"%PDF-"):
            raise ValueError("Only a valid PDF invoice can be processed.")
        if not contents or len(contents) > MAX_PDF_BYTES:
            raise ValueError("PDF must be nonempty and no larger than 12 MB.")
        source_id, sha = save_source(filename, contents, "invoice_pdf")
        event(run_id, stage, "completed", "Original invoice stored with a SHA-256 fingerprint.",
              {"filename": filename, "bytes": len(contents), "source_id": source_id})

        stage = STAGES[1]
        event(run_id, stage, "running", "Extracting selectable PDF text; OCR is attempted if the PDF is image-only.")
        try:
            with fitz.open(stream=contents, filetype="pdf") as pdf:
                if pdf.page_count < 1:
                    raise ValueError("The PDF contains no pages.")
                selectable = sum(len(page.get_text("text").strip()) for page in pdf)
                page_count = pdf.page_count
        except Exception as exc:
            raise ValueError(f"PDF could not be read: {exc}") from exc
        report = parse_invoice_pdf(contents, filename, source_id)
        method = "text" if selectable else "ocr" if report.invoices else "ocr_unavailable"
        event(run_id, stage, "completed" if report.invoices else "failed",
              "Text extraction completed." if report.invoices else "No readable text was recovered; the case needs review.",
              {"method": method, "pages": page_count, "warnings": report.messages})

        stage = STAGES[2]
        event(run_id, stage, "running", "Converting source text to validated invoice fields.")
        record = report.invoices[0] if report.invoices else InvoiceRecord(
            id=new_id("inv"), source_file_id=source_id, original_filename=filename,
            raw_text="", flags=["ocr_unavailable"],
        )
        invoice_id = record.id
        model_data: dict = {}
        fields = extract_fields(record, page_texts=report.page_texts)
        if record.raw_text and any(getattr(fields, key) is None for key in (
            "vendor_name", "invoice_number", "invoice_date", "po_number", "total_amount",
        )):
            if ollama_status().get("available"):
                model_data = extract_invoice_fields_with_ollama(record.raw_text)
                try:
                    assisted = extract_fields(record, model_data, report.page_texts)
                    if assisted.model_dump() != fields.model_dump():
                        fields = assisted
                        fields.warnings.append("model_assisted_extraction")
                        method += "+ollama"
                except ValidationError:
                    fields.warnings.append("llm_schema_invalid")
        fields.warnings.extend(report.messages)
        insert_invoices([record])
        update_run(run_id, "running", invoice_id=invoice_id)
        event(run_id, stage, "completed", "Invoice fields extracted; missing values remain empty.",
              {"invoice_number": fields.invoice_number, "po_number": fields.po_number,
               "total_amount": str(fields.total_amount) if fields.total_amount is not None else None,
               "method": method, "warnings": fields.warnings}, invoice_id)

        stage = STAGES[3]
        event(run_id, stage, "running", "Checking required values, dates and money fields.", invoice_id=invoice_id)
        missing = [key for key in ("vendor_name", "invoice_number", "invoice_date", "total_amount") if getattr(fields, key) is None]
        event(run_id, stage, "completed", "Core fields validated." if not missing else "Required fields are missing; review will be required.",
              {"missing": missing}, invoice_id)

        stage = STAGES[4]
        event(run_id, stage, "running", "Looking up the vendor master and eligibility.", invoice_id=invoice_id)
        vendor_list = vendors()
        vendor_matches = [v for v in vendor_list if normalized_name(v.legal_name) == normalized_name(fields.vendor_name) and fields.vendor_name]
        event(run_id, stage, "completed", "Vendor master lookup completed.",
              {"matches": [v.legal_name for v in vendor_matches],
               "eligible": bool(len(vendor_matches) == 1 and vendor_matches[0].approved and vendor_matches[0].active)}, invoice_id)

        stage = STAGES[5]
        event(run_id, stage, "running", "Finding the referenced PO or controlled vendor candidates.", invoice_id=invoice_id)
        pos = purchase_orders()
        po_candidates = [p.number for p in pos if normalized(p.number) == normalized(fields.po_number)] if fields.po_number else [p.number for p in pos if vendor_matches and p.vendor_id == vendor_matches[0].id and p.status == "open"]
        event(run_id, stage, "completed", "Purchase order candidates identified.",
              {"candidates": po_candidates, "explicit_reference": bool(fields.po_number)}, invoice_id)

        stage = STAGES[6]
        event(run_id, stage, "running", "Comparing source hash, vendor and invoice identity against history.", invoice_id=invoice_id)
        previous = previous_invoices()
        possible = [p["invoice_id"] for p in previous if p["source_sha256"] == sha or (
            fields.invoice_number and normalized(p["invoice_number"]) == normalized(fields.invoice_number)
            and normalized_name(p["vendor_name"]) == normalized_name(fields.vendor_name))]
        event(run_id, stage, "completed", "Prior invoice search completed.", {"possible_prior_records": possible}, invoice_id)

        stage = STAGES[7]
        event(run_id, stage, "running", "Calculating PO balance, tolerance, tax and line-item checks.", invoice_id=invoice_id)
        decision = evaluate_invoice(fields, vendor_list, pos, previous, sha)
        event(run_id, stage, "completed", "Deterministic financial checks completed.",
              {"checks": [c.model_dump() for c in decision.checks],
               "remaining_po_balance": str(decision.remaining_po_balance) if decision.remaining_po_balance is not None else None}, invoice_id)

        stage = STAGES[8]
        event(run_id, stage, "running", "Applying procurement decision rules; no model decides approval.", invoice_id=invoice_id)
        event(run_id, stage, "completed", decision.explanation,
              {"suggested_status": decision.suggested_status, "reason_codes": decision.reason_codes}, invoice_id)

        stage = STAGES[9]
        event(run_id, stage, "running", "Saving the suggestion, source evidence and run history.", invoice_id=invoice_id)
        save_approval(invoice_id, run_id, sha, method, fields, decision)
        set_source_status(source_id, "processed")
        # The payment suggestion is distinct from approval; recalculate without deleting reviews.
        run_reconciliation_and_store()
        event(run_id, stage, "completed", "Approval result and payment tracking link persisted.",
              {"invoice_id": invoice_id}, invoice_id)
        update_run(run_id, "completed", invoice_id=invoice_id)
    except Exception as exc:
        message = str(exc)[:500]
        event(run_id, stage, "failed", message, invoice_id=invoice_id)
        if source_id:
            set_source_status(source_id, "failed", message)
        update_run(run_id, "failed", invoice_id=invoice_id, error=message)


def parse_vendor_csv(contents: bytes) -> list[Vendor]:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Vendor CSV must be UTF-8.") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("Vendor CSV contains no records.")
    result = []
    for row in rows:
        result.append(Vendor(
            id=(row.get("vendor_id") or row.get("id") or "").strip(),
            legal_name=(row.get("legal_name") or row.get("vendor_name") or "").strip(),
            gstin=(row.get("gstin") or "").strip() or None,
            approved=(row.get("approved") or "yes").strip().lower() in {"yes", "true", "1", "approved"},
            active=(row.get("active") or "yes").strip().lower() in {"yes", "true", "1", "active"},
        ))
    if any(not v.id or not v.legal_name for v in result):
        raise ValueError("Every vendor requires vendor_id and legal_name.")
    return result


def parse_po_csv(contents: bytes) -> list[PurchaseOrder]:
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("PO CSV must be UTF-8.") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("PO CSV contains no records.")
    result = []
    for row in rows:
        raw_lines = row.get("line_items_json") or "[]"
        try:
            lines = [LineItem.model_validate(item) for item in json.loads(raw_lines)]
        except (ValueError, ValidationError, TypeError) as exc:
            raise ValueError(f"Invalid line_items_json for PO {row.get('po_number')}: {exc}") from exc
        result.append(PurchaseOrder(
            number=(row.get("po_number") or row.get("number") or "").strip(),
            vendor_id=(row.get("vendor_id") or "").strip(),
            po_date=row.get("po_date") or None,
            status=(row.get("status") or "open").strip().lower(),
            currency=(row.get("currency") or "INR").strip().upper(),
            approved_amount=Decimal(row.get("approved_amount") or "0"),
            opening_invoiced_amount=Decimal(row.get("opening_invoiced_amount") or "0"),
            tolerance_percent=Decimal(row.get("tolerance_percent") or "0"),
            tolerance_amount=Decimal(row.get("tolerance_amount") or "0"),
            tax_treatment=(row.get("tax_treatment") or "unspecified").strip().lower(),
            tax_rate=Decimal(row["tax_rate"]) if row.get("tax_rate") else None,
            line_items=lines,
        ))
    if any(not p.number or not p.vendor_id or p.approved_amount <= 0 for p in result):
        raise ValueError("Every PO requires po_number, vendor_id and a positive approved_amount.")
    return result


def import_procurement(vendor_bytes: bytes | None, po_bytes: bytes | None) -> dict:
    parsed_vendors = parse_vendor_csv(vendor_bytes) if vendor_bytes else []
    parsed_pos = parse_po_csv(po_bytes) if po_bytes else []
    known_vendor_ids = {v.id for v in (vendors() + parsed_vendors)}
    unknown = [p.vendor_id for p in parsed_pos if p.vendor_id not in known_vendor_ids]
    if unknown:
        raise ValueError(f"PO references unknown vendor IDs: {', '.join(sorted(set(unknown)))}")
    upsert_procurement(parsed_vendors, parsed_pos)
    return {"vendors": len(parsed_vendors), "purchase_orders": len(parsed_pos)}
