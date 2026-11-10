from __future__ import annotations

from datetime import date
from decimal import Decimal

import fitz
from fastapi.testclient import TestClient

from app.approval import InvoiceFields, LineItem, PurchaseOrder, Vendor, evaluate_invoice, extract_fields
from app.approval_pipeline import STAGES, import_procurement
from app.approval_store import init_approval_db, purchase_orders, upsert_procurement
from app.approval_store import approval_rows
from app.database import add_review_decision, connect, init_db, latest_reviews_by_result, reset_workspace
from app.demo_data import SAMPLES, seed_demo_workspace
from app.main import app
from app.models import InvoiceRecord


def vendor(*, approved=True) -> Vendor:
    return Vendor(id="v1", legal_name="Asha Office Supplies", gstin="29ASHA1234A1Z5", approved=approved)


def po(**overrides) -> PurchaseOrder:
    values = dict(number="PO-100", vendor_id="v1", po_date=date(2026, 1, 1),
                  approved_amount=Decimal("10000"), tolerance_percent=Decimal("1"),
                  tax_rate=Decimal("0"), line_items=[LineItem(description="Office stationery")])
    values.update(overrides)
    return PurchaseOrder(**values)


def fields(**overrides) -> InvoiceFields:
    values = dict(vendor_name="Asha Office Supplies", gstin="29ASHA1234A1Z5",
                  invoice_number="INV-100", invoice_date=date(2026, 1, 5), po_number="PO-100",
                  currency="INR", subtotal=Decimal("10000"), tax_amount=Decimal("0"),
                  total_amount=Decimal("10000"), line_items=[LineItem(description="Office stationery")])
    values.update(overrides)
    return InvoiceFields(**values)


def evaluate(f: InvoiceFields | None = None, p: list[PurchaseOrder] | None = None,
             previous: list[dict] | None = None, v: list[Vendor] | None = None,
             sha: str | None = None):
    return evaluate_invoice(f or fields(), v if v is not None else [vendor()],
                            p if p is not None else [po()], previous or [], sha)


def test_exact_match_approved():
    result = evaluate()
    assert result.suggested_status == "approved"
    assert result.matched_po == "PO-100"


def test_within_tolerance_approved():
    result = evaluate(fields(subtotal=Decimal("10050"), total_amount=Decimal("10050")))
    assert result.suggested_status == "approved"
    assert any(check.code == "within_tolerance" for check in result.checks)


def test_amount_beyond_tolerance_needs_review():
    result = evaluate(fields(subtotal=Decimal("10200"), total_amount=Decimal("10200")))
    assert result.suggested_status == "needs_review"
    assert "po_balance_exceeded" in result.reason_codes


def test_unapproved_vendor_rejected():
    assert evaluate(v=[vendor(approved=False)]).suggested_status == "rejected"


def test_missing_po_reference_uses_controlled_implied_match():
    result = evaluate(fields(po_number=None))
    assert result.suggested_status == "needs_review"
    assert result.matched_po == "PO-100"
    assert "implied_po_match" in result.reason_codes


def test_ambiguous_po_candidates_needs_review():
    second = po(number="PO-101")
    result = evaluate(fields(po_number=None), p=[po(), second])
    assert result.suggested_status == "needs_review"
    assert set(result.candidate_pos) == {"PO-100", "PO-101"}


def test_duplicate_invoice_rejected():
    previous = [dict(invoice_id="inv_prev", vendor_name="Asha Office Supplies",
                     invoice_number="INV-100", invoice_date="2026-01-05", total_amount="10000",
                     matched_po="PO-100", effective_status="approved", source_sha256="other")]
    result = evaluate(previous=previous)
    assert result.suggested_status == "rejected"
    assert result.duplicate_of == "inv_prev"


def test_valid_split_invoice_consumes_only_remaining_balance():
    previous = [dict(invoice_id="inv_prev", vendor_name="Asha Office Supplies",
                     invoice_number="INV-099", invoice_date="2026-01-04", total_amount="4000",
                     matched_po="PO-100", effective_status="approved", source_sha256="other")]
    result = evaluate(fields(invoice_number="INV-101", subtotal=Decimal("5000"),
                             total_amount=Decimal("5000")), previous=previous)
    assert result.suggested_status == "approved"
    assert result.remaining_po_balance == Decimal("6000")


def test_split_invoice_exceeding_remaining_balance_needs_review():
    previous = [dict(invoice_id="inv_prev", vendor_name="Asha Office Supplies",
                     invoice_number="INV-099", invoice_date="2026-01-04", total_amount="6000",
                     matched_po="PO-100", effective_status="approved", source_sha256="other")]
    result = evaluate(fields(invoice_number="INV-101", subtotal=Decimal("5000"),
                             total_amount=Decimal("5000")), previous=previous)
    assert result.suggested_status == "needs_review"
    assert "po_balance_exceeded" in result.reason_codes


def test_tax_mismatch_needs_review():
    result = evaluate(fields(subtotal=Decimal("9000"), tax_amount=Decimal("1000")))
    assert result.suggested_status == "needs_review"
    assert "tax_rate_mismatch" in result.reason_codes


def test_missing_invoice_number_needs_review():
    assert evaluate(fields(invoice_number=None)).suggested_status == "needs_review"


def test_malformed_date_and_amount_remain_missing():
    record = InvoiceRecord(id="inv_bad", vendor_name="Asha Office Supplies", raw_text="Invoice Date: nonsense\nGrand Total: unclear")
    extracted = extract_fields(record)
    assert extracted.invoice_date is None
    assert extracted.total_amount is None
    assert evaluate(extracted).suggested_status == "needs_review"


def test_line_item_mismatch_needs_review():
    result = evaluate(fields(line_items=[LineItem(description="Server hosting")]))
    assert result.suggested_status == "needs_review"
    assert "line_item_mismatch" in result.reason_codes


def test_itemized_totals_must_match_invoice_subtotal():
    item = LineItem(description="Office stationery", quantity=Decimal("1"),
                    unit_price=Decimal("9000"), line_total=Decimal("9000"))
    result = evaluate(fields(line_items=[item]))
    assert result.suggested_status == "needs_review"
    assert "line_total_mismatch" in result.reason_codes


def test_negative_extracted_amount_requires_review():
    result = evaluate(fields(tax_amount=Decimal("-1")))
    assert result.suggested_status == "needs_review"
    assert "negative_tax_amount" in result.reason_codes


def test_model_assisted_values_require_source_review():
    result = evaluate(fields(warnings=["model_assisted_extraction"]))
    assert result.suggested_status == "needs_review"
    assert "model_extraction_review" in result.reason_codes


def test_repeated_source_document_rejected():
    previous = [dict(invoice_id="inv_prev", vendor_name="Different Vendor",
                     invoice_number="INV-099", invoice_date="2026-01-04", total_amount="1000",
                     matched_po="PO-100", effective_status="approved", source_sha256="same-sha")]
    result = evaluate(previous=previous, sha="same-sha")
    assert result.suggested_status == "rejected"
    assert "repeated_document" in result.reason_codes


def test_closed_po_rejected():
    result = evaluate(p=[po(status="closed")])
    assert result.suggested_status == "rejected"


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 80), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_pipeline_persists_stages_review_history_and_export(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "approval.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")
    init_db()
    init_approval_db()
    reset_workspace()
    upsert_procurement([vendor()], [po()])
    pdf = make_pdf("Vendor: Asha Office Supplies\nInvoice Number: INV-100\nInvoice Date: 2026-01-05\nPO Number: PO-100\nDescription: Office stationery\nSubtotal: INR 10000.00\nGST: INR 0.00\nGrand Total: INR 10000.00")
    client = TestClient(app)
    upload = client.post("/api/approval/process", files={"invoices": ("invoice.pdf", pdf, "application/pdf")})
    assert upload.status_code == 200
    run_id = upload.json()["runs"][0]["run_id"]
    run = client.get(f"/api/approval/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert {e["stage"] for e in run["events"] if e["status"] == "completed"} == set(STAGES)
    invoice_id = run["invoice_id"]
    detail = client.get(f"/api/approval/invoices/{invoice_id}").json()
    assert detail["suggested_status"] == "approved"
    assert client.get(f"/api/source-files/{detail['source_file_id']}").content == pdf
    preview = client.get(f"/api/source-files/{detail['source_file_id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")
    review = client.post(f"/api/approval/invoices/{invoice_id}/review", json={
        "action": "approve", "corrections": {"invoice_number": "INV-100-C"},
        "selected_po": "PO-100", "note": "Verified source document"})
    assert review.status_code == 200
    after = client.get(f"/api/approval/invoices/{invoice_id}").json()
    assert after["extraction"]["invoice_number"] == "INV-100"
    assert after["reviews"][0]["corrections"]["invoice_number"] == "INV-100-C"
    assert "Verified source document" in client.get("/api/approval/export").text
    duplicate = client.post("/api/approval/process", files={"invoices": ("again.pdf", pdf, "application/pdf")})
    second_id = client.get(f"/api/approval/runs/{duplicate.json()['runs'][0]['run_id']}").json()["invoice_id"]
    assert client.get(f"/api/approval/invoices/{second_id}").json()["suggested_status"] == "rejected"


def test_unreadable_pdf_has_failed_run_without_fake_invoice(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "unreadable.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")
    init_db()
    init_approval_db()
    reset_workspace()
    client = TestClient(app)
    response = client.post("/api/approval/process", files={"invoices": ("bad.pdf", b"%PDF-not-a-real-file", "application/pdf")})
    run_id = response.json()["runs"][0]["run_id"]
    run = client.get(f"/api/approval/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["invoice_id"] is None
    assert client.get("/api/approval/invoices").json()["invoices"] == []


def test_image_only_pdf_without_ocr_becomes_review_case(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "scan.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")
    monkeypatch.setattr("app.parsing._ocr_pdf_text", lambda document: [])
    init_db(); init_approval_db(); reset_workspace()
    document = fitz.open(); document.new_page(); pdf = document.tobytes(); document.close()
    client = TestClient(app)
    response = client.post("/api/approval/process", files={"invoices": ("scan.pdf", pdf, "application/pdf")})
    run = client.get(f"/api/approval/runs/{response.json()['runs'][0]['run_id']}").json()
    assert run["status"] == "completed"
    assert any(event["stage"] == "text_extraction" and event["status"] == "failed" for event in run["events"])
    detail = client.get(f"/api/approval/invoices/{run['invoice_id']}").json()
    assert detail["suggested_status"] == "needs_review"
    assert detail["extraction"]["invoice_number"] is None


def test_procurement_csv_import_and_po_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "procurement.sqlite3"))
    init_db(); init_approval_db(); reset_workspace()
    vendors_csv = b"vendor_id,legal_name,gstin,approved,active\nv1,Asha Office Supplies,29ASHA1234A1Z5,yes,yes\n"
    pos_csv = b"po_number,vendor_id,po_date,status,currency,approved_amount,opening_invoiced_amount,tolerance_percent,tolerance_amount,tax_treatment,tax_rate,line_items_json\nPO-100,v1,2026-01-01,open,INR,10000,0,1,0,exclusive,0,[]\n"
    result = import_procurement(vendors_csv, pos_csv)
    assert result == {"vendors": 1, "purchase_orders": 1}
    assert purchase_orders()[0].number == "PO-100"


def test_new_invoice_does_not_erase_existing_payment_review(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "linked.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")
    init_db(); init_approval_db(); reset_workspace()
    upsert_procurement([vendor()], [po()])
    client = TestClient(app)
    first_pdf = make_pdf("Vendor: Asha Office Supplies\nInvoice Number: INV-100\nInvoice Date: 2026-01-05\nPO Number: PO-100\nDescription: Office stationery\nSubtotal: INR 10000\nGST: INR 0\nGrand Total: INR 10000")
    first = client.post("/api/approval/process", files={"invoices": ("first.pdf", first_pdf, "application/pdf")})
    first_id = client.get(f"/api/approval/runs/{first.json()['runs'][0]['run_id']}").json()["invoice_id"]
    result_id = f"rec_{first_id}"
    add_review_decision(result_id, "approve", "unpaid", [], "Keep this human decision")
    second_pdf = make_pdf("Vendor: Asha Office Supplies\nInvoice Number: INV-101\nInvoice Date: 2026-01-06\nPO Number: PO-100\nDescription: Office stationery\nSubtotal: INR 5000\nGST: INR 0\nGrand Total: INR 5000")
    client.post("/api/approval/process", files={"invoices": ("second.pdf", second_pdf, "application/pdf")})
    assert latest_reviews_by_result()[result_id]["note"] == "Keep this human decision"
    assert client.get("/api/summary").json()["total_invoices"] == 2


def test_recording_demo_starts_small_and_live_uploads_preserve_both_workflows(monkeypatch, tmp_path):
    monkeypatch.setenv("FINGUARD_DB", str(tmp_path / "recording-demo.sqlite3"))
    monkeypatch.setenv("FINGUARD_DISABLE_OLLAMA", "1")

    with TestClient(app) as client:
        def upload_invoice(relative_path: str) -> tuple[dict, dict]:
            path = SAMPLES / relative_path
            response = client.post("/api/approval/process", files={
                "invoices": (path.name, path.read_bytes(), "application/pdf")
            })
            assert response.status_code == 200
            run = client.get(f"/api/approval/runs/{response.json()['runs'][0]['run_id']}").json()
            assert run["status"] == "completed"
            return run, client.get(f"/api/approval/invoices/{run['invoice_id']}").json()

        initial = {row["original_filename"]: row for row in approval_rows()}
        assert set(initial) == {
            "01_metro_clean.pdf", "02_kaveri_tax_discrepancy.pdf", "04_blue_lotus_split_one.pdf"
        }
        assert initial["01_metro_clean.pdf"]["suggested_status"] == "approved"
        assert initial["04_blue_lotus_split_one.pdf"]["suggested_status"] == "approved"
        assert "tax_rate_mismatch" in initial["02_kaveri_tax_discrepancy.pdf"]["reason_codes"]
        assert client.get("/api/approval/summary").json()["total_invoices"] == 3
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 3
            assert conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 7
            assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 8
        catalog = client.get("/api/demo/catalog").json()
        assert {row["filename"] for row in catalog["invoices"] if row["processed"]} == set(initial)
        assert catalog["bank_statement"]["processed"] is False

        _, bundled = upload_invoice("03_pixelcraft_bundled.pdf")
        assert bundled["suggested_status"] == "approved"
        _, second = upload_invoice("05_blue_lotus_split_two.pdf")
        assert second["suggested_status"] == "approved"
        _, excess = upload_invoice("06_blue_lotus_over_balance.pdf")
        assert excess["suggested_status"] == "needs_review"
        assert "po_balance_exceeded" in excess["suggestion"]["reason_codes"]
        assert excess["suggestion"]["remaining_po_balance"] == "6000.00"

        bank_path = SAMPLES / "bank_statement.csv"
        bank_file = {"bank_csv": (bank_path.name, bank_path.read_bytes(), "text/csv")}
        bank_upload = client.post("/api/upload", files=bank_file).json()
        assert bank_upload["bank_transactions"] == 5
        payment_rows = client.get("/api/reconciliations").json()["results"]
        by_number = {row["invoice_number"]: row for row in payment_rows if row.get("invoice_number")}
        assert by_number["MFS-908"]["outcome"] == "matched"
        assert by_number["KPM-331"]["outcome"] == "partially_paid"
        assert by_number["BLP-9001"]["outcome"] == "matched"
        assert by_number["BLP-9002"]["outcome"] == "partially_paid"
        assert any(row["outcome"] == "unmatched_bank" for row in payment_rows)
        assert client.get(f"/api/approval/invoices/{second['invoice_id']}").json()["payment"]["outcome"] == "partially_paid"
        assert client.get(f"/api/approval/invoices/{second['invoice_id']}").json()["suggested_status"] == "approved"
        repeat_bank = client.post("/api/upload", files=bank_file).json()
        assert repeat_bank["bank_transactions"] == 0
        assert "already imported" in " ".join(repeat_bank["messages"])
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 5

        _, duplicate = upload_invoice("07_metro_duplicate.pdf")
        assert duplicate["suggested_status"] == "rejected"
        assert duplicate["suggestion"]["duplicate_of"] == initial["01_metro_clean.pdf"]["invoice_id"]
        scan_run, scan = upload_invoice("08_cedar_scanned_missing_number.pdf")
        assert scan["suggested_status"] == "needs_review"
        assert scan["extraction"]["invoice_number"] is None
        extraction_stage = next(event for event in scan_run["events"] if event["stage"] == "text_extraction" and event["status"] in {"completed", "failed"})
        assert extraction_stage["output"]["method"] in {"ocr", "ocr_unavailable"}
        _, held_out = upload_invoice("live_upload/09_blue_lotus_live_upload.pdf")
        assert held_out["suggested_status"] == "approved"
        assert held_out["matched_po"] == "PO-PACK-402"
        assert client.get("/api/approval/summary").json()["total_invoices"] == 9
        full_catalog = client.get("/api/demo/catalog").json()
        assert all(row["processed"] for row in full_catalog["invoices"])
        assert full_catalog["bank_statement"]["processed"] is True

        review = client.post(f"/api/approval/invoices/{initial['02_kaveri_tax_discrepancy.pdf']['invoice_id']}/review", json={
            "action": "reject", "note": "Tax evidence requires correction"
        })
        assert review.status_code == 200
        for _ in range(2):
            reset = client.post("/api/demo").json()
            assert reset["invoices"] == 3
            assert reset["bank_transactions"] == 0
            assert {row["original_filename"] for row in approval_rows()} == set(initial)
            with connect() as conn:
                assert conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM approval_reviews").fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM approval_runs").fetchone()[0] == 3
            restored_catalog = client.get("/api/demo/catalog").json()
            assert {row["filename"] for row in restored_catalog["invoices"] if row["processed"]} == set(initial)
            assert restored_catalog["bank_statement"]["processed"] is False
