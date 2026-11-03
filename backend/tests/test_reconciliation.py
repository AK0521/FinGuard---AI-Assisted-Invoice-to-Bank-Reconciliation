from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models import BankTransactionRecord, InvoiceRecord
from app.reconciliation import reconcile_records


def inv(
    idx: str = "1",
    *,
    vendor: str | None = "Asha Office Supplies",
    number: str | None = "INV-100",
    amount: str = "10000.00",
    inv_date: date = date(2026, 1, 1),
) -> InvoiceRecord:
    return InvoiceRecord(
        id=f"inv_{idx}",
        vendor_name=vendor,
        invoice_number=number,
        invoice_date=inv_date,
        total_amount=Decimal(amount),
        tax_amount=Decimal("0.00"),
        original_filename=f"invoice-{idx}.pdf",
        page_number=1,
    )


def tx(
    idx: str = "1",
    *,
    description: str = "NEFT Asha Office Supplies INV-100",
    amount: str = "10000.00",
    tx_date: date = date(2026, 1, 10),
) -> BankTransactionRecord:
    return BankTransactionRecord(
        id=f"txn_{idx}",
        transaction_date=tx_date,
        description=description,
        debit_amount=Decimal(amount),
        credit_amount=Decimal("0.00"),
        reference=f"UTR-{idx}",
        row_number=int(idx) + 1 if idx.isdigit() else None,
    )


def test_exact_match() -> None:
    result = reconcile_records([inv()], [tx()])[0]
    assert result.outcome == "matched"
    assert result.selected_transaction_ids == ["txn_1"]
    assert result.outstanding_amount == Decimal("0.00")


def test_partial_payment() -> None:
    result = reconcile_records(
        [inv(amount="42000.00", number="KPM-331", vendor="Kaveri Paper Mart")],
        [tx(description="Partial payment Kaveri Paper Mart KPM-331", amount="15000.00")],
    )[0]
    assert result.outcome == "partially_paid"
    assert result.matched_amount == Decimal("15000.00")
    assert result.outstanding_amount == Decimal("27000.00")


def test_unpaid_invoice() -> None:
    result = reconcile_records([inv()], [])[0]
    assert result.outcome == "unpaid"
    assert "no_candidate_found" in result.reason_codes


def test_unmatched_bank_transaction() -> None:
    results = reconcile_records([], [tx(description="Card purchase Hardware World", amount="9600.00")])
    assert len(results) == 1
    assert results[0].outcome == "unmatched_bank"


def test_duplicate_invoice_candidate_needs_review() -> None:
    invoice_a = inv("1", number="DUP-42")
    invoice_b = inv("2", number="DUP-42")
    results = reconcile_records([invoice_a, invoice_b], [tx(description="NEFT Asha Office Supplies DUP-42")])
    assert {result.outcome for result in results if result.invoice_id} == {"needs_review"}
    assert all("duplicate_invoice_candidate" in result.reason_codes for result in results if result.invoice_id)


def test_vendor_name_variation_match() -> None:
    result = reconcile_records(
        [inv(vendor="Navrang Logistics LLP", number="NL-778", amount="27600.00")],
        [tx(description="NEFT NAVRANG LOGI payment", amount="27600.00")],
    )[0]
    assert result.outcome == "matched"
    assert "vendor_name_match" in result.reason_codes


def test_transaction_outside_allowed_date_window() -> None:
    late_tx = tx(tx_date=date(2026, 1, 1) + timedelta(days=70))
    results = reconcile_records([inv()], [late_tx])
    invoice_result = next(result for result in results if result.invoice_id)
    assert invoice_result.outcome == "unpaid"


def test_amount_mismatch_needs_review() -> None:
    result = reconcile_records([inv()], [tx(amount="9950.00")])[0]
    assert result.outcome == "needs_review"
    assert "amount_mismatch" in result.reason_codes or "amount_near_mismatch" in result.reason_codes


def test_missing_invoice_number_needs_review() -> None:
    result = reconcile_records(
        [inv(number=None)],
        [tx(description="NEFT Asha Office Supplies", amount="10000.00")],
    )[0]
    assert result.outcome == "needs_review"
    assert "missing_invoice_number" in result.reason_codes


def test_ambiguous_candidate_match() -> None:
    result = reconcile_records([inv()], [tx("1"), tx("2")])[0]
    assert result.outcome == "needs_review"
    assert "ambiguous_candidates" in result.reason_codes
    assert len(result.candidate_transaction_ids) == 2

