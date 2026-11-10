from __future__ import annotations

from decimal import Decimal

import fitz
from fastapi.testclient import TestClient

from app.database import add_review_decision, init_db, latest_reviews_by_result, reset_workspace
from app.demo_data import SAMPLES
from app.main import app
from app.parsing import parse_bank_csv, parse_invoice_pdf


def setup_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "finguard-test.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")
    init_db()
    reset_workspace()


def test_csv_parsing_common_bank_columns() -> None:
    csv_bytes = (
        "Date,Description,Debit,Credit,Reference\n"
        "2026-01-10,NEFT Asha INV-100,10000,,UTR-1\n"
    ).encode()
    report = parse_bank_csv(csv_bytes, "bank.csv", "src_1")
    assert not report.messages
    assert report.transactions[0].debit_amount == Decimal("10000.00")
    assert report.transactions[0].row_number == 2


def test_pdf_extraction_text_based_invoice() -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Vendor: Asha Office Supplies\nInvoice Number: INV-100\nInvoice Date: 2026-01-01\nGST: INR 1525.42\nGrand Total: INR 10000.00\nCurrency: INR",
    )
    contents = doc.tobytes()
    report = parse_invoice_pdf(contents, "invoice.pdf", "src_1")
    assert report.invoices[0].vendor_name == "Asha Office Supplies"
    assert report.invoices[0].total_amount == Decimal("10000.00")
    assert report.invoices[0].page_number == 1


def test_sentence_layout_does_not_swallow_vendor_fields() -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Asha Office Supplies issued bill INV-777 on 2026-04-08. Purchase order PO-100.")
    report = parse_invoice_pdf(doc.tobytes(), "irregular.pdf", "src_1")
    doc.close()
    assert report.invoices[0].vendor_name == "Asha Office Supplies"
    assert report.invoices[0].invoice_date is None  # The model stage may fill this later.


def test_pdf_extraction_reports_scanned_or_empty_pdf() -> None:
    doc = fitz.open()
    doc.new_page()
    report = parse_invoice_pdf(doc.tobytes(), "blank.pdf", "src_1")
    assert report.invoices == []
    assert "no selectable text" in report.messages[0]


def test_review_decision_persistence(monkeypatch, tmp_path) -> None:
    setup_temp_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.post("/api/demo")
        bank = SAMPLES / "bank_statement.csv"
        upload = client.post("/api/upload", files={"bank_csv": (bank.name, bank.read_bytes(), "text/csv")})
        assert upload.json()["bank_transactions"] == 5
        results = client.get("/api/reconciliations").json()["results"]
    review_target = next(result for result in results if result.get("invoice_number") == "KPM-331")
    assert review_target["outcome"] == "partially_paid"
    add_review_decision(review_target["id"], "reject", "unpaid", [], "Reviewed in test")
    latest = latest_reviews_by_result()
    assert latest[review_target["id"]]["final_status"] == "unpaid"
    assert latest[review_target["id"]]["note"] == "Reviewed in test"


def test_cited_qa_uses_source_references(monkeypatch, tmp_path) -> None:
    setup_temp_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.post("/api/demo")
        empty = client.post("/api/ask", json={"question": "Which bank transactions have no matching invoice?"}).json()
        assert "No unmatched outgoing bank transactions" in empty["answer"]
        bank = SAMPLES / "bank_statement.csv"
        upload = client.post("/api/upload", files={"bank_csv": (bank.name, bank.read_bytes(), "text/csv")})
        assert upload.json()["bank_transactions"] == 5
        response = client.post("/api/ask", json={"question": "Which bank transactions have no matching invoice?"})
    data = response.json()
    assert "Unmatched outgoing bank transactions" in data["answer"]
    assert data["citations"]
    assert data["citations"][0]["locator"].startswith("CSV row")


def test_csv_export_contains_review_and_suggestions(monkeypatch, tmp_path) -> None:
    setup_temp_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.post("/api/demo")
        response = client.get("/api/export")
    body = response.text
    assert response.status_code == 200
    assert "suggested_outcome" in body
    assert "final_status" in body
    assert "Metro Facility Services" in body

