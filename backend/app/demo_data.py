from __future__ import annotations

from datetime import date
from decimal import Decimal

from .database import (
    connect,
    create_source_file,
    insert_bank_transactions,
    insert_invoices,
    reset_workspace,
    run_reconciliation_and_store,
)
from .models import BankTransactionRecord, InvoiceRecord


def _invoice_text(vendor: str, number: str, invoice_date: date, tax: Decimal, total: Decimal) -> str:
    return "\n".join(
        [
            f"Vendor: {vendor}",
            f"Invoice Number: {number}",
            f"Invoice Date: {invoice_date.isoformat()}",
            f"GST/Tax: INR {tax}",
            f"Grand Total: INR {total}",
            "Currency: INR",
        ]
    )


def seed_sample_workspace_if_empty() -> dict[str, int | str]:
    with connect() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM invoices) AS invoices,
              (SELECT COUNT(*) FROM bank_transactions) AS bank_transactions,
              (SELECT COUNT(*) FROM reconciliation_results) AS results
            """
        ).fetchone()
    if counts and (counts["invoices"] or counts["bank_transactions"] or counts["results"]):
        return {
            "message": "Existing workspace retained.",
            "invoices": counts["invoices"],
            "bank_transactions": counts["bank_transactions"],
            "reconciliation_results": counts["results"],
        }
    return seed_demo_workspace()


def seed_demo_workspace() -> dict[str, int | str]:
    reset_workspace()
    bank_source_jan = create_source_file("bank_statement_january.csv", "sample_bank_csv", "processed")
    bank_source_feb = create_source_file("bank_statement_february.csv", "sample_bank_csv", "processed")

    invoice_specs = [
        (
            "Metro Facility Services",
            "MFS-908",
            date(2026, 1, 16),
            Decimal("1800.00"),
            Decimal("11800.00"),
            "01_metro_facility_services.pdf",
        ),
        (
            "Kaveri Paper Mart",
            "KPM-331",
            date(2026, 1, 12),
            Decimal("6406.78"),
            Decimal("42000.00"),
            "02_kaveri_paper_mart.pdf",
        ),
        (
            "PixelCraft Design Studio",
            "PCD-2026-009",
            date(2026, 1, 14),
            Decimal("4957.63"),
            Decimal("32500.00"),
            "03_pixelcraft_design_studio.pdf",
        ),
        (
            "Blue Lotus Packaging",
            "BLP-9001",
            date(2026, 2, 5),
            Decimal("1952.54"),
            Decimal("12800.00"),
            "04_blue_lotus_packaging_unstructured.pdf",
        ),
        (
            "Cedar Cleaning Care",
            "CFC-778",
            date(2026, 2, 8),
            Decimal("1449.15"),
            Decimal("9500.00"),
            "05_cedar_cleaning_care_scanned.pdf",
        ),
    ]

    invoices: list[InvoiceRecord] = []
    invoice_texts = {
        "MFS-908": "\n".join(
            [
                "Metro Facility Services",
                "Tax Invoice",
                "Invoice No: MFS-908",
                "Invoice Date: 16 Jan 2026",
                "Bill To: FinGuard Evaluation Lab",
                "Description: Monthly facility maintenance",
                "Subtotal: INR 10,000.00",
                "GST: INR 1,800.00",
                "Total Amount: INR 11,800.00",
                "Payment terms: Net 10 days",
            ]
        ),
        "KPM-331": "\n".join(
            [
                "Kaveri Paper Mart",
                "Tax Invoice",
                "Invoice Number: KPM-331",
                "Invoice Date: 12 Jan 2026",
                "Bill To: FinGuard Evaluation Lab",
                "Description: Copier paper and stationery bulk order",
                "Subtotal: INR 35,593.22",
                "GST: INR 6,406.78",
                "Grand Total: INR 42,000.00",
            ]
        ),
        "PCD-2026-009": "\n".join(
            [
                "PixelCraft Design Studio",
                "Invoice Number: PCD-2026-009",
                "Invoice Date: 14 Jan 2026",
                "Client: FinGuard Evaluation Lab",
                "Description: Dashboard prototype design sprint",
                "Subtotal: INR 27,542.37",
                "Tax: INR 4,957.63",
                "Amount Due: INR 32,500.00",
            ]
        ),
        "BLP-9001": "\n".join(
            [
                "Blue Lotus Packaging",
                "Hello FinGuard Evaluation Lab,",
                "This memo confirms packaging material delivered on 05 February 2026.",
                "Our billing reference for your records is BLP-9001.",
                "Please arrange settlement of INR 12,800.00 including all applicable taxes.",
                "Tax component included in the above amount is INR 1,952.54.",
                "Items supplied: corrugated cartons, labels, protective sleeves.",
            ]
        ),
        "CFC-778": "\n".join(
            [
                "Cedar Cleaning Care",
                "Invoice Number: CFC-778",
                "Invoice Date: 08 Feb 2026",
                "Client: FinGuard Evaluation Lab",
                "Service: Deep cleaning and sanitation support",
                "Subtotal: INR 8,050.85",
                "GST: INR 1,449.15",
                "Total Amount: INR 9,500.00",
                "Payment due within 5 days",
            ]
        ),
    }
    for index, (vendor, number, inv_date, tax, total, filename) in enumerate(invoice_specs, start=1):
        source_id = create_source_file(filename, "sample_invoice_pdf", "processed")
        invoices.append(
            InvoiceRecord(
                id=f"sample_inv_{index}",
                source_file_id=source_id,
                original_filename=filename,
                vendor_name=vendor,
                invoice_number=number,
                invoice_date=inv_date,
                total_amount=total,
                tax_amount=tax,
                currency="INR",
                page_number=1,
                raw_text=invoice_texts.get(number, _invoice_text(vendor, number, inv_date, tax, total)),
                flags=[],
            )
        )

    transactions = [
        BankTransactionRecord(
            id="sample_txn_1",
            source_file_id=bank_source_jan,
            original_filename="bank_statement_january.csv",
            transaction_date=date(2026, 1, 22),
            description="Partial payment Kaveri Paper Mart KPM-331",
            debit_amount=Decimal("15000.00"),
            reference="UTR-EVAL-1001",
            row_number=2,
            flags=[],
        ),
        BankTransactionRecord(
            id="sample_txn_2",
            source_file_id=bank_source_jan,
            original_filename="bank_statement_january.csv",
            transaction_date=date(2026, 1, 24),
            description="Card purchase Hardware World Bengaluru",
            debit_amount=Decimal("9600.00"),
            reference="CARD-EVAL-1002",
            row_number=3,
            flags=[],
        ),
        BankTransactionRecord(
            id="sample_txn_3",
            source_file_id=bank_source_jan,
            original_filename="bank_statement_january.csv",
            transaction_date=date(2026, 1, 27),
            description="NEFT Metro Facility Services MFS-908",
            debit_amount=Decimal("11800.00"),
            reference="UTR-EVAL-1003",
            row_number=4,
            flags=[],
        ),
        BankTransactionRecord(
            id="sample_txn_4",
            source_file_id=bank_source_jan,
            original_filename="bank_statement_january.csv",
            transaction_date=date(2026, 1, 28),
            description="Duplicate NEFT Metro Facility Services MFS-908",
            debit_amount=Decimal("11800.00"),
            reference="UTR-EVAL-1004",
            row_number=5,
            flags=[],
        ),
        BankTransactionRecord(
            id="sample_txn_5",
            source_file_id=bank_source_feb,
            original_filename="bank_statement_february.csv",
            transaction_date=date(2026, 2, 10),
            description="NEFT Blue Lotus Packaging BLP-9001",
            debit_amount=Decimal("12800.00"),
            reference="UTR-EVAL-2001",
            row_number=2,
            flags=[],
        ),
        BankTransactionRecord(
            id="sample_txn_6",
            source_file_id=bank_source_feb,
            original_filename="bank_statement_february.csv",
            transaction_date=date(2026, 2, 12),
            description="NEFT Cedar Cleaning Care CFC-778",
            debit_amount=Decimal("9500.00"),
            reference="UTR-EVAL-2002",
            row_number=3,
            flags=[],
        ),
    ]

    insert_invoices(invoices)
    insert_bank_transactions(transactions)
    results = run_reconciliation_and_store()
    return {
        "message": "Sample evaluation workspace loaded.",
        "invoices": len(invoices),
        "bank_transactions": len(transactions),
        "reconciliation_results": len(results),
    }

