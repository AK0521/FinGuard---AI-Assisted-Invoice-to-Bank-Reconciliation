from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal

import fitz

from .database import new_id
from .llm import extract_invoice_fields_with_ollama
from .models import BankTransactionRecord, InvoiceRecord
from .normalization import money, parse_date


@dataclass
class ParseReport:
    invoices: list[InvoiceRecord] = field(default_factory=list)
    transactions: list[BankTransactionRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _first_match(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            return re.sub(r"\s+", " ", value)
    return None


def _split_invoice_markers(text: str) -> str:
    markers = [
        "Tax Invoice",
        "Invoice Number",
        "Invoice No",
        "Invoice Date",
        "Bill To",
        "Client",
        "Description",
        "Service",
        "Subtotal",
        "GST",
        "Total Amount",
        "Grand Total",
        "Amount Due",
        "Payment due",
    ]
    pattern = r"\s+(" + "|".join(re.escape(marker) for marker in markers) + r")\b"
    return re.sub(pattern, r"\n\1", text, flags=re.IGNORECASE)


def _extract_vendor(text: str) -> str | None:
    labelled = _first_match([r"(?:Vendor|Supplier|Bill From|From)\s*[:\-]\s*(.+)"], text)
    if labelled:
        return labelled

    skip_markers = [
        "invoice",
        "bill to",
        "client",
        "description",
        "service",
        "subtotal",
        "total",
        "gst",
        "tax",
        "payment",
        "currency",
        "thank you",
        "ocr testing",
        "image-only pdf",
    ]
    for line in _split_invoice_markers(text).splitlines():
        candidate = re.sub(r"\s+", " ", line.strip(" :-\t"))
        if len(candidate) < 3:
            continue
        lowered = candidate.lower()
        if any(marker in lowered for marker in skip_markers):
            continue
        if re.search(r"[A-Za-z]", candidate):
            return candidate[:120]
    return None


def _extract_total_amount(text: str) -> str | None:
    amount = r"(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    normalized = _split_invoice_markers(text)
    return _first_match(
        [
            rf"^\s*(?:Grand\s*Total|Total\s*Amount|Amount\s*Due|Invoice\s*Total|Net\s*Payable|Balance\s*Due)\s*[:\-]?\s*{amount}\b",
            rf"\b(?:Grand\s*Total|Total\s*Amount|Amount\s*Due|Invoice\s*Total|Net\s*Payable|Balance\s*Due)\s*[:\-]?\s*{amount}\b",
            rf"^\s*Total\s*[:\-]?\s*{amount}\b",
        ],
        normalized,
    )


def parse_invoice_pdf(contents: bytes, filename: str, source_file_id: str) -> ParseReport:
    report = ParseReport()
    try:
        doc = fitz.open(stream=contents, filetype="pdf")
    except Exception as exc:  # pragma: no cover - depends on malformed binary internals
        report.messages.append(f"{filename}: PDF could not be opened ({exc}).")
        return report

    page_texts: list[tuple[int, str]] = []
    for index, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if text:
            page_texts.append((index, text))

    if not page_texts:
        ocr_text = _ocr_pdf_text(doc)
        if ocr_text:
            page_texts = [(1, ocr_text)]
            report.messages.append(f"{filename}: text was extracted with local OCR because selectable PDF text was absent.")
        else:
            report.messages.append(
                f"{filename}: no selectable text was found and local OCR could not extract readable text."
            )
            return report

    full_text = "\n".join(text for _, text in page_texts)
    first_page = page_texts[0][0]
    vendor = _extract_vendor(full_text)
    invoice_number = _first_match(
        [
            r"(?:Invoice\s*(?:Number|No\.?|#)|Inv\s*No\.?)\s*[:\-]\s*([A-Z0-9][A-Z0-9\-\/]+)",
            r"\b(INV[-\/]?[A-Z0-9\-\/]+)\b",
        ],
        full_text,
    )
    invoice_date_raw = _first_match(
        [
            r"(?:Invoice\s*Date|Date)\s*[:\-]\s*([0-9]{1,2}[\/\-.][0-9]{1,2}[\/\-.][0-9]{2,4})",
            r"(?:Invoice\s*Date|Date)\s*[:\-]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",
            r"(?:Invoice\s*Date|Date)\s*[:\-]\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})",
        ],
        full_text,
    )
    total_raw = _extract_total_amount(full_text)
    tax_raw = _first_match(
        [
            r"(?:GST|Tax|IGST|CGST\s*\+\s*SGST)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9,]+(?:\.[0-9]{1,2})?)",
        ],
        full_text,
    )
    currency = _first_match([r"(?:Currency)\s*[:\-]\s*([A-Z]{3})"], full_text) or "INR"

    if not all([vendor, invoice_number, invoice_date_raw, total_raw]):
        llm_fields = extract_invoice_fields_with_ollama(full_text)
        vendor = vendor or llm_fields.get("vendor_name")
        invoice_number = invoice_number or llm_fields.get("invoice_number")
        invoice_date_raw = invoice_date_raw or llm_fields.get("invoice_date")
        total_raw = total_raw or llm_fields.get("total_amount")
        tax_raw = tax_raw or llm_fields.get("tax_amount")
        currency = currency or llm_fields.get("currency") or "INR"

    flags: list[str] = []
    invoice_date = parse_date(invoice_date_raw)
    total_amount = money(total_raw)
    tax_amount = money(tax_raw)
    if not vendor:
        flags.append("missing_vendor")
    if not invoice_number:
        flags.append("missing_invoice_number")
    if not invoice_date:
        flags.append("missing_invoice_date")
    if total_amount is None:
        flags.append("missing_total_amount")

    report.invoices.append(
        InvoiceRecord(
            id=new_id("inv"),
            source_file_id=source_file_id,
            original_filename=filename,
            vendor_name=vendor,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            total_amount=total_amount,
            tax_amount=tax_amount,
            currency=currency.upper(),
            page_number=first_page,
            raw_text=full_text[:12000],
            flags=flags,
        )
    )
    return report


def _ocr_pdf_text(doc: fitz.Document) -> str:
    try:
        import easyocr
        import numpy as np
        from PIL import Image
    except Exception:
        return ""

    try:
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception:
        return ""

    chunks: list[str] = []
    for page in doc:
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            results = reader.readtext(np.array(image), detail=0, paragraph=False)
        except Exception:
            continue
        chunks.extend(str(item) for item in results if str(item).strip())
    return "\n".join(chunks).strip()


def _pick(row: dict[str, str], aliases: list[str]) -> str | None:
    normalized = {key.strip().lower(): value for key, value in row.items() if key is not None}
    for alias in aliases:
        if alias.lower() in normalized:
            value = normalized[alias.lower()]
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def parse_bank_csv(contents: bytes, filename: str, source_file_id: str) -> ParseReport:
    report = ParseReport()
    text = contents.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        report.messages.append(f"{filename}: CSV has no header row.")
        return report

    for index, row in enumerate(reader, start=2):
        date_raw = _pick(row, ["date", "transaction date", "txn date", "value date"])
        description = _pick(row, ["description", "particulars", "narration", "details"]) or ""
        debit_raw = _pick(row, ["debit", "withdrawal", "withdrawals", "paid out", "dr"])
        credit_raw = _pick(row, ["credit", "deposit", "deposits", "paid in", "cr"])
        amount_raw = _pick(row, ["amount"])
        reference = _pick(row, ["reference", "ref", "transaction id", "utr", "txn id", "cheque/ref no"])

        debit = money(debit_raw) or Decimal("0.00")
        credit = money(credit_raw) or Decimal("0.00")
        if amount_raw and debit == 0 and credit == 0:
            amount = money(amount_raw) or Decimal("0.00")
            if amount < 0:
                debit = abs(amount)
            else:
                credit = amount

        flags: list[str] = []
        tx_date = parse_date(date_raw)
        if not tx_date:
            flags.append("missing_transaction_date")
        if not description:
            flags.append("missing_description")
        if debit == 0 and credit == 0:
            flags.append("missing_amount")

        report.transactions.append(
            BankTransactionRecord(
                id=new_id("txn"),
                source_file_id=source_file_id,
                original_filename=filename,
                transaction_date=tx_date,
                description=description or "Unlabeled bank transaction",
                debit_amount=debit,
                credit_amount=credit,
                reference=reference,
                row_number=index,
                flags=flags,
            )
        )

    if not report.transactions:
        report.messages.append(f"{filename}: CSV contained no transaction rows.")
    return report

