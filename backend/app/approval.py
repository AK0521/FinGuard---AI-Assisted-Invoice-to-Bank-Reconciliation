"""Deterministic invoice-to-PO evaluation. AI may extract fields, never decide status."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from .models import InvoiceRecord
from .normalization import money, parse_date


ZERO = Decimal("0.00")
CENT = Decimal("0.01")
Decision = Literal["approved", "needs_review", "rejected"]


class Vendor(BaseModel):
    id: str
    legal_name: str
    gstin: str | None = None
    approved: bool = True
    active: bool = True


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class PurchaseOrder(BaseModel):
    number: str
    vendor_id: str
    po_date: date | None = None
    status: Literal["open", "closed", "cancelled"] = "open"
    currency: str = "INR"
    approved_amount: Decimal
    opening_invoiced_amount: Decimal = ZERO
    tolerance_percent: Decimal = ZERO
    tolerance_amount: Decimal = ZERO
    tax_treatment: Literal["inclusive", "exclusive", "unspecified"] = "unspecified"
    tax_rate: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)


class InvoiceFields(BaseModel):
    vendor_name: str | None = None
    vendor_address: str | None = None
    gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    taxable_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    tax_rate: Decimal | None = None
    total_amount: Decimal | None = None
    tax_inclusive: bool | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    field_pages: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Check(BaseModel):
    code: str
    outcome: Literal["pass", "warning", "fail"]
    explanation: str


class ApprovalDecision(BaseModel):
    suggested_status: Decision
    matched_po: str | None = None
    vendor_id: str | None = None
    candidate_pos: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    difference: Decimal | None = None
    tolerance: Decimal | None = None
    remaining_po_balance: Decimal | None = None
    duplicate_of: str | None = None


def normalized(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def normalized_name(value: str | None) -> str:
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    return " ".join(word for word in words if word not in {"pvt", "private", "ltd", "limited", "llp"})


def _label(text: str, labels: str) -> str | None:
    match = re.search(rf"(?im)^\s*(?:{labels})\s*[:#-]\s*([^\r\n]+)", text)
    return match.group(1).strip() if match else None


def _amount(text: str, labels: str) -> Decimal | None:
    value = _label(text, labels)
    if value is None:
        return None
    match = re.search(r"(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", value, re.I)
    return money(match.group(1)) if match else None


def extract_fields(record: InvoiceRecord, llm_data: dict | None = None,
                   page_texts: list[tuple[int, str]] | None = None) -> InvoiceFields:
    """Parse visible evidence; model values only fill gaps and remain reviewable."""
    text = record.raw_text or ""
    model = llm_data or {}
    po = _label(text, r"(?:Purchase\s*Order|PO)(?:\s*(?:No\.?|Number|Reference|Ref))?")
    if po:
        po = po.split()[0].strip(".,;")
    line_items: list[LineItem] = []
    for match in re.finditer(
        r"(?im)^\s*Item\s*:\s*(.+?)\s*\|\s*Qty\s*:\s*([0-9.]+)\s*\|\s*Unit\s*:\s*(?:INR\s*)?([0-9,.]+)\s*\|\s*Amount\s*:\s*(?:INR\s*)?([0-9,.]+)",
        text,
    ):
        line_items.append(LineItem(
            description=match.group(1).strip(), quantity=money(match.group(2)),
            unit_price=money(match.group(3)), line_total=money(match.group(4)),
        ))
    if not line_items:
        description = _label(text, r"(?:Description|Service|Items?\s*supplied)")
        if description:
            line_items.append(LineItem(description=description))
    if not line_items and isinstance(model.get("line_items"), list):
        for item in model["line_items"][:30]:
            try:
                line_items.append(LineItem.model_validate(item))
            except Exception:
                pass
    subtotal = _amount(text, r"(?:Subtotal|Sub\s*total|Taxable\s*amount)")
    tax = record.tax_amount if record.tax_amount is not None else _amount(text, r"(?:GST|Tax|IGST|CGST\s*\+\s*SGST)")
    total = record.total_amount
    page = record.page_number or 1
    raw_date = _label(text, r"(?:Invoice\s*Date|Date)")
    due_date = parse_date(_label(text, r"(?:Due\s*Date|Payment\s*Due)"))
    rate_raw = _label(text, r"(?:GST|Tax)\s*Rate")
    rate_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", rate_raw or "")
    rate = money(rate_match.group(1)) if rate_match else None
    inclusive = bool(re.search(r"(?i)(?:tax|gst)\s+included|inclusive\s+of\s+(?:tax|gst)", text))
    exclusive = bool(re.search(r"(?i)(?:tax|gst)\s+extra|exclusive\s+of\s+(?:tax|gst)", text))
    fields = InvoiceFields(
        vendor_name=record.vendor_name or model.get("vendor_name"),
        vendor_address=_label(text, r"(?:Vendor\s*Address|Address)") or model.get("vendor_address"),
        gstin=_label(text, r"(?:GSTIN|Tax\s*Registration)") or model.get("gstin"),
        invoice_number=record.invoice_number or model.get("invoice_number"),
        invoice_date=record.invoice_date or parse_date(raw_date) or parse_date(model.get("invoice_date")),
        due_date=due_date or parse_date(model.get("due_date")),
        po_number=po or model.get("po_number"),
        currency=record.currency or model.get("currency"),
        subtotal=subtotal if subtotal is not None else money(model.get("subtotal")),
        taxable_amount=_amount(text, r"Taxable\s*amount") or money(model.get("taxable_amount")),
        tax_amount=tax if tax is not None else money(model.get("tax_amount")),
        tax_rate=rate or money(model.get("tax_rate")),
        total_amount=total if total is not None else money(model.get("total_amount")),
        tax_inclusive=True if inclusive else False if exclusive else model.get("tax_inclusive"),
        line_items=line_items,
        field_pages={},
        warnings=list(record.flags),
    )
    # Page references refer to the first page of the extracted source. Missing values have no citation.
    field_labels = {
        "vendor_name": r"(?:Vendor|Supplier|Bill From)",
        "gstin": r"(?:GSTIN|Tax\s*Registration)",
        "invoice_number": r"Invoice\s*(?:Number|No)",
        "invoice_date": r"Invoice\s*Date",
        "po_number": r"(?:PO|Purchase\s*Order)\s*(?:Number|No|Ref)?",
        "subtotal": r"(?:Subtotal|Sub\s*total|Taxable\s*amount)",
        "total_amount": r"(?:Grand\s*Total|Total\s*Amount|Amount\s*Due)",
        "tax_amount": r"(?:GST|Tax)\s*:",
    }
    if page_texts:
        for key, pattern in field_labels.items():
            if getattr(fields, key, None) is None:
                continue
            for number, text_on_page in page_texts:
                value = str(getattr(fields, key))
                if value.lower() in text_on_page.lower() or re.search(pattern, text_on_page, re.I):
                    fields.field_pages[key] = number
                    break
    else:
        fields.field_pages = {key: page for key in field_labels if getattr(fields, key, None) is not None and record.page_number}
    return fields


def evaluate_invoice(
    fields: InvoiceFields,
    vendors: list[Vendor],
    purchase_orders: list[PurchaseOrder],
    previous: list[dict],
    source_sha256: str | None = None,
) -> ApprovalDecision:
    checks: list[Check] = []
    hard: list[str] = []
    review: list[str] = []

    def add(code: str, outcome: Literal["pass", "warning", "fail"], explanation: str, *, stop: bool = False) -> None:
        checks.append(Check(code=code, outcome=outcome, explanation=explanation))
        if stop:
            hard.append(code)
        elif outcome != "pass":
            review.append(code)

    for key, label in (("vendor_name", "vendor"), ("invoice_number", "invoice number"), ("invoice_date", "invoice date"), ("total_amount", "invoice total")):
        if getattr(fields, key) is None:
            add(f"missing_{key}", "warning", f"The {label} could not be verified from the document.")
    if fields.total_amount is not None and fields.total_amount <= ZERO:
        add("invalid_total", "fail", "Invoice total must be greater than zero.", stop=True)
    for key, label in (("subtotal", "subtotal"), ("taxable_amount", "taxable amount"), ("tax_amount", "tax amount")):
        value = getattr(fields, key)
        if value is not None and value < ZERO:
            add(f"negative_{key}", "warning", f"The extracted {label} is negative; verify the source document.")
    if fields.invoice_date is None and fields.warnings:
        add("extraction_uncertain", "warning", "Extraction is incomplete; inspect the original PDF before approval.")
    if "model_assisted_extraction" in fields.warnings:
        add("model_extraction_review", "warning", "A local model supplied invoice fields; verify those values against the source PDF.")

    vendor: Vendor | None = None
    exact = [v for v in vendors if normalized(v.gstin) and normalized(v.gstin) == normalized(fields.gstin)] if fields.gstin else []
    if not exact:
        exact = [v for v in vendors if normalized_name(v.legal_name) == normalized_name(fields.vendor_name) and fields.vendor_name]
    if len(exact) == 1:
        vendor = exact[0]
        add("vendor_identified", "pass", f"Vendor master record {vendor.legal_name} matches the invoice.")
    elif len(exact) > 1:
        add("ambiguous_vendor", "warning", "Multiple vendor master records match this invoice.")
    else:
        near = [v for v in vendors if SequenceMatcher(None, normalized_name(v.legal_name), normalized_name(fields.vendor_name)).ratio() >= .78 and fields.vendor_name]
        if len(near) == 1:
            vendor = near[0]
            add("approximate_vendor", "warning", f"Vendor name resembles {vendor.legal_name}; confirm identity manually.")
        else:
            add("vendor_not_verified", "warning", "No unique approved vendor identity could be verified.")
    if vendor and (not vendor.approved or not vendor.active):
        add("vendor_ineligible", "fail", f"{vendor.legal_name} is unapproved or inactive in the vendor master.", stop=True)

    normalized_po = normalized(fields.po_number)
    exact_pos = [po for po in purchase_orders if normalized(po.number) == normalized_po] if normalized_po else []
    candidates: list[PurchaseOrder] = []
    po: PurchaseOrder | None = None
    if exact_pos:
        po = exact_pos[0]
        candidates = exact_pos
        add("exact_po_reference", "pass", f"The invoice explicitly references {po.number}.")
    elif fields.po_number:
        add("po_reference_not_found", "warning", f"PO reference {fields.po_number} is absent from the register.")
    else:
        candidates = [p for p in purchase_orders if vendor and p.vendor_id == vendor.id and p.status == "open" and p.currency.upper() == (fields.currency or "INR").upper()]
        if fields.invoice_date:
            candidates = [p for p in candidates if not p.po_date or p.po_date <= fields.invoice_date]
        if len(candidates) == 1:
            po = candidates[0]
            add("implied_po_match", "warning", f"{po.number} is the only eligible PO for this vendor; confirm the implied match.")
        elif len(candidates) > 1:
            add("ambiguous_po", "warning", f"{len(candidates)} open POs are plausible; select the intended PO.")
        else:
            add("po_missing", "warning", "No eligible PO could be identified.")

    difference: Decimal | None = None
    tolerance: Decimal | None = None
    remaining: Decimal | None = None
    if po:
        if vendor and po.vendor_id != vendor.id:
            add("po_vendor_mismatch", "fail", f"{po.number} belongs to another vendor.", stop=True)
        if po.status != "open":
            add("po_not_open", "fail", f"{po.number} is {po.status}.", stop=True)
        if fields.currency and fields.currency.upper() != po.currency.upper():
            add("currency_mismatch", "warning", f"Invoice currency {fields.currency} differs from PO currency {po.currency}.")
        consumed = po.opening_invoiced_amount + sum(
            (money(item.get("total_amount")) or ZERO) for item in previous
            if item.get("matched_po") == po.number and item.get("effective_status") == "approved"
        )
        remaining = po.approved_amount - consumed
        tolerance = max(po.tolerance_amount, (po.approved_amount * po.tolerance_percent / Decimal("100"))).quantize(CENT)
        if fields.total_amount is not None:
            difference = fields.total_amount - remaining
            if difference > tolerance:
                add("po_balance_exceeded", "warning", f"Invoice {fields.total_amount:.2f} exceeds remaining PO balance {remaining:.2f} by {difference:.2f}; tolerance is {tolerance:.2f}.")
            elif difference > ZERO:
                add("within_tolerance", "pass", f"Invoice exceeds remaining PO balance by {difference:.2f}, within tolerance {tolerance:.2f}.")
            else:
                add("po_balance_ok", "pass", f"Invoice {fields.total_amount:.2f} is within remaining PO balance {remaining:.2f}.")

    duplicate_of = None
    for item in previous:
        if source_sha256 and item.get("source_sha256") == source_sha256:
            duplicate_of = item.get("invoice_id")
            add("repeated_document", "fail", f"This exact PDF was already uploaded as {duplicate_of}.", stop=True)
            break
        same_vendor = normalized_name(item.get("vendor_name")) == normalized_name(fields.vendor_name) and fields.vendor_name
        if same_vendor and fields.invoice_number and normalized(item.get("invoice_number")) == normalized(fields.invoice_number):
            duplicate_of = item.get("invoice_id")
            add("confirmed_duplicate", "fail", f"Vendor and invoice number match earlier record {duplicate_of}.", stop=True)
            break
        if same_vendor and fields.invoice_date and str(item.get("invoice_date")) == fields.invoice_date.isoformat() and money(item.get("total_amount")) == fields.total_amount:
            duplicate_of = item.get("invoice_id")
            add("possible_duplicate", "warning", f"Vendor, date and amount match earlier record {duplicate_of}.")
            break

    if fields.subtotal is not None and fields.tax_amount is not None and fields.total_amount is not None:
        delta = abs(fields.subtotal + fields.tax_amount - fields.total_amount)
        if delta > CENT:
            add("tax_arithmetic_mismatch", "warning", f"Subtotal {fields.subtotal:.2f} plus tax {fields.tax_amount:.2f} differs from total {fields.total_amount:.2f} by {delta:.2f}.")
        else:
            add("tax_arithmetic_ok", "pass", "Subtotal plus tax equals the invoice total.")
    elif fields.tax_amount is not None and fields.total_amount is not None:
        add("tax_basis_unverified", "warning", "The tax basis is missing, so tax arithmetic cannot be verified.")
    if po and po.tax_rate is not None and fields.subtotal is not None and fields.tax_amount is not None:
        expected = (fields.subtotal * po.tax_rate / Decimal("100")).quantize(CENT)
        if abs(expected - fields.tax_amount) > CENT:
            add("tax_rate_mismatch", "warning", f"PO tax rate {po.tax_rate}% implies {expected:.2f}, but invoice tax is {fields.tax_amount:.2f}.")
    if po and fields.tax_inclusive is not None and po.tax_treatment != "unspecified":
        expected_inclusive = po.tax_treatment == "inclusive"
        if fields.tax_inclusive != expected_inclusive:
            add("tax_treatment_mismatch", "warning", f"Invoice tax treatment differs from PO policy ({po.tax_treatment}).")
    if po and po.line_items and fields.line_items:
        po_names = [normalized_name(line.description) for line in po.line_items]
        missing_lines = [line.description for line in fields.line_items if max((SequenceMatcher(None, normalized_name(line.description), name).ratio() for name in po_names), default=0) < .55]
        if missing_lines:
            add("line_item_mismatch", "warning", f"Invoice line items not clearly represented on the PO: {', '.join(missing_lines[:3])}.")
        else:
            add("line_items_match", "pass", "Invoice descriptions align with PO line items.")
    elif po and po.line_items:
        add("line_items_unverified", "warning", "Line items could not be reliably extracted for comparison.")
    for line in fields.line_items:
        if any(value is not None and value < ZERO for value in (line.quantity, line.unit_price, line.line_total)):
            add("negative_line_value", "warning", f"Line {line.description} contains a negative quantity or amount.")
        if line.quantity is not None and line.unit_price is not None and line.line_total is not None:
            if abs(line.quantity * line.unit_price - line.line_total) > CENT:
                add("line_arithmetic_mismatch", "warning", f"Line {line.description} quantity × unit price differs from line total.")
    if fields.line_items and all(line.line_total is not None for line in fields.line_items):
        basis = fields.subtotal
        if basis is None and fields.total_amount is not None and fields.tax_amount is not None:
            basis = fields.total_amount - fields.tax_amount
        if basis is not None:
            line_sum = sum((line.line_total for line in fields.line_items if line.line_total is not None), ZERO)
            if abs(line_sum - basis) > CENT:
                add("line_total_mismatch", "warning", f"Itemized lines total {line_sum:.2f}, but the invoice pre-tax amount is {basis:.2f}.")
            else:
                add("line_totals_match", "pass", "Itemized line totals match the invoice pre-tax amount.")

    status: Decision = "rejected" if hard else "needs_review" if review else "approved"
    codes = [check.code for check in checks if check.outcome != "pass"]
    if status == "approved":
        explanation = f"Approved for payment: vendor {vendor.legal_name if vendor else 'unknown'} and PO {po.number if po else 'unknown'} are valid; amount, tax and available evidence pass the configured checks."
    elif status == "rejected":
        explanation = "Rejected by procurement policy: " + " ".join(check.explanation for check in checks if check.code in hard)
    else:
        explanation = "Needs analyst review: " + " ".join(check.explanation for check in checks if check.code in review)
    return ApprovalDecision(
        suggested_status=status, matched_po=po.number if po else None,
        vendor_id=vendor.id if vendor else None, candidate_pos=[p.number for p in candidates],
        checks=checks, reason_codes=codes, explanation=explanation,
        difference=difference, tolerance=tolerance, remaining_po_balance=remaining,
        duplicate_of=duplicate_of,
    )
