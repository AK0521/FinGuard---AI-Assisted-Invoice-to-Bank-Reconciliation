from __future__ import annotations

import csv
import hashlib
import io
import fitz
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .database import (
    add_review_decision,
    connect,
    fetch_invoices,
    init_db,
    insert_bank_transactions,
    json_load,
    latest_reviews_by_result,
    reset_workspace,
    row_to_dict,
    run_reconciliation_and_store,
)
from .approval import InvoiceFields, evaluate_invoice
from .approval_pipeline import import_procurement, process_pdf
from .approval_store import (
    add_approval_review, approval_detail, approval_rows, create_run, init_approval_db,
    previous_invoices, purchase_orders, run_detail, runs, save_source, set_source_status,
    source_document, vendors,
)
from .demo_data import sample_catalog, seed_demo_workspace, seed_sample_workspace_if_empty
from .llm import ollama_status
from .normalization import as_money
from .parsing import parse_bank_csv
from .retrieval import answer_question


app = FastAPI(title="FinGuard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "correct"]
    final_status: Literal["matched", "partially_paid", "unpaid", "unmatched_bank", "needs_review"]
    selected_transaction_ids: list[str] = Field(default_factory=list)
    matched_amount: Decimal | None = None
    outstanding_amount: Decimal | None = None
    note: str | None = None


class AskRequest(BaseModel):
    question: str
    result_id: str | None = None


class ApprovalReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    corrections: dict[str, object] = Field(default_factory=dict)
    selected_po: str | None = None
    note: str = ""


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    init_approval_db()
    seed_sample_workspace_if_empty()


@app.get("/api/health")
def health() -> dict[str, str]:
    init_db()
    return {"status": "ok"}


@app.get("/api/llm/status")
def llm_status() -> dict[str, object]:
    return ollama_status()


@app.post("/api/demo")
def load_demo() -> dict[str, int | str]:
    init_db()
    return seed_demo_workspace()


@app.get("/api/demo/catalog")
def demo_catalog() -> dict[str, object]:
    init_db()
    init_approval_db()
    return sample_catalog()


@app.post("/api/reset")
def reset() -> dict[str, str]:
    init_db()
    reset_workspace()
    return {"message": "Workspace reset."}


@app.post("/api/upload")
async def upload_workspace(
    invoices: list[UploadFile] = File(default=[]),
    bank_csv: list[UploadFile] = File(default=[]),
) -> dict[str, object]:
    init_db()
    init_approval_db()
    parsed_transactions = []
    messages: list[str] = []
    processed_invoices = 0
    seen_bank_hashes: set[str] = set()

    for invoice_file in invoices:
        contents = await invoice_file.read()
        filename = Path(invoice_file.filename or "invoice.pdf").name
        run_id = create_run(filename)
        process_pdf(run_id, filename, contents)
        run = run_detail(run_id)
        if run and run["status"] == "completed":
            processed_invoices += 1
        else:
            messages.append(f"{filename}: {run['error'] if run else 'processing failed'}")

    for bank_file in bank_csv:
        contents = await bank_file.read()
        filename = Path(bank_file.filename or "bank.csv").name
        if not filename.lower().endswith(".csv") or len(contents) > 8 * 1024 * 1024:
            messages.append(f"{filename}: expected a CSV no larger than 8 MB.")
            continue
        sha = hashlib.sha256(contents).hexdigest()
        with connect() as conn:
            already_imported = conn.execute("""
                SELECT 1 FROM source_files sf
                JOIN bank_transactions bt ON bt.source_file_id = sf.id
                WHERE sf.source_type = 'bank_csv' AND sf.sha256 = ?
                  AND sf.status = 'processed' LIMIT 1
            """, (sha,)).fetchone()
        if already_imported or sha in seen_bank_hashes:
            messages.append(f"{filename}: already imported; duplicate CSV skipped.")
            continue
        source_id, _ = save_source(filename, contents, "bank_csv")
        report = parse_bank_csv(contents, filename, source_id)
        parsed_transactions.extend(report.transactions)
        messages.extend(report.messages)
        set_source_status(source_id, "processed" if report.transactions else "failed", " ".join(report.messages))
        if report.transactions:
            seen_bank_hashes.add(sha)

    if parsed_transactions:
        insert_bank_transactions(parsed_transactions)
    results = run_reconciliation_and_store()

    return {
        "message": "Files added to the current workspace." if processed_invoices or parsed_transactions else "No new files imported.",
        "messages": messages,
        "invoices": processed_invoices,
        "bank_transactions": len(parsed_transactions),
        "reconciliation_results": len(results),
    }


def _status_with_review(result: dict, reviews: dict[str, dict]) -> str:
    review = reviews.get(result["id"])
    return review["final_status"] if review else result["outcome"]


def _apply_review_fields(data: dict, review: dict | None) -> dict:
    data["suggested_matched_amount"] = data["matched_amount"]
    data["suggested_outstanding_amount"] = data["outstanding_amount"]
    if review:
        data["final_status"] = review["final_status"]
        data["review_action"] = review["action"]
        data["review_note"] = review["note"]
        if review.get("final_matched_amount") is not None:
            data["matched_amount"] = review["final_matched_amount"]
        if review.get("final_outstanding_amount") is not None:
            data["outstanding_amount"] = review["final_outstanding_amount"]
    else:
        data["final_status"] = data["outcome"]
        data["review_action"] = None
        data["review_note"] = None
    return data


def _result_rows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                rr.*,
                inv.vendor_name,
                inv.invoice_number,
                inv.invoice_date,
                inv.total_amount AS invoice_total,
                inv.original_filename AS invoice_filename,
                bt.description AS bank_description,
                bt.transaction_date AS bank_transaction_date,
                bt.debit_amount AS bank_debit_amount,
                bt.reference AS bank_reference,
                bt.row_number AS bank_row_number
            FROM reconciliation_results rr
            LEFT JOIN invoices inv ON inv.id = rr.invoice_id
            LEFT JOIN bank_transactions bt ON bt.id = rr.bank_transaction_id
            ORDER BY
                CASE rr.outcome
                    WHEN 'needs_review' THEN 0
                    WHEN 'partially_paid' THEN 1
                    WHEN 'unpaid' THEN 2
                    WHEN 'unmatched_bank' THEN 3
                    ELSE 4
                END,
                inv.invoice_date,
                bt.transaction_date
            """
        ).fetchall()
    reviews = latest_reviews_by_result()
    results = []
    for row in rows:
        data = row_to_dict(row) or {}
        review = reviews.get(data["id"])
        data["candidate_transaction_ids"] = json_load(data.pop("candidate_transaction_ids_json"), [])
        original_selected_ids = json_load(data.pop("selected_transaction_ids_json"), [])
        review_selected_ids = json_load(review["selected_transaction_ids_json"], []) if review else []
        data["original_selected_transaction_ids"] = original_selected_ids
        data["selected_transaction_ids"] = review_selected_ids or original_selected_ids
        data["reason_codes"] = json_load(data.pop("reason_codes_json"), [])
        data["needs_review"] = bool(data["needs_review"])
        data = _apply_review_fields(data, review)
        results.append(data)
    return results


@app.get("/api/summary")
def summary() -> dict[str, object]:
    init_db()
    invoices = fetch_invoices()
    rows = _result_rows()
    invoice_rows = [row for row in rows if row.get("invoice_id")]
    status_breakdown: dict[str, int] = {}
    for row in rows:
        status = row["final_status"]
        status_breakdown[status] = status_breakdown.get(status, 0) + 1

    reconciled = sum((Decimal(row["matched_amount"]) for row in invoice_rows), Decimal("0.00"))
    outstanding = sum((Decimal(row["outstanding_amount"]) for row in invoice_rows), Decimal("0.00"))
    review_count = sum(1 for row in rows if row["needs_review"] or row.get("review_action") is not None)
    return {
        "dataset_label": "Local reconciliation workspace.",
        "total_invoices": len(invoices),
        "reconciled_amount": as_money(reconciled),
        "outstanding_amount": as_money(outstanding),
        "items_needing_review": review_count,
        "status_breakdown": status_breakdown,
        "total_results": len(rows),
    }


@app.get("/api/reconciliations")
def reconciliations(search: str = "", status: str = "all") -> dict[str, object]:
    init_db()
    rows = _result_rows()
    search_lower = search.lower().strip()
    if status != "all":
        rows = [row for row in rows if row["final_status"] == status or row["outcome"] == status]
    if search_lower:
        rows = [
            row
            for row in rows
            if search_lower
            in " ".join(
                str(row.get(key) or "")
                for key in [
                    "vendor_name",
                    "invoice_number",
                    "reason_text",
                    "bank_description",
                    "bank_reference",
                ]
            ).lower()
        ]
    return {"results": rows}


def _bank_rows_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    with connect() as conn:
        rows = []
        for tx_id in ids:
            row = row_to_dict(conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (tx_id,)).fetchone())
            if row:
                row["flags"] = json_load(row.pop("flags_json"), [])
                rows.append(row)
    return rows


@app.get("/api/reconciliations/{result_id}")
def reconciliation_detail(result_id: str) -> dict[str, object]:
    init_db()
    with connect() as conn:
        result = row_to_dict(conn.execute("SELECT * FROM reconciliation_results WHERE id = ?", (result_id,)).fetchone())
        if not result:
            raise HTTPException(status_code=404, detail="Reconciliation result not found.")
        invoice = None
        if result.get("invoice_id"):
            invoice = row_to_dict(conn.execute("SELECT * FROM invoices WHERE id = ?", (result["invoice_id"],)).fetchone())
            if invoice:
                invoice["flags"] = json_load(invoice.pop("flags_json"), [])
        bank = None
        if result.get("bank_transaction_id"):
            bank = row_to_dict(
                conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (result["bank_transaction_id"],)).fetchone()
            )
            if bank:
                bank["flags"] = json_load(bank.pop("flags_json"), [])
        reviews = [
            row_to_dict(row)
            for row in conn.execute(
                "SELECT * FROM review_decisions WHERE result_id = ? ORDER BY created_at DESC",
                (result_id,),
            ).fetchall()
        ]

    result["candidate_transaction_ids"] = json_load(result.pop("candidate_transaction_ids_json"), [])
    original_selected_ids = json_load(result.pop("selected_transaction_ids_json"), [])
    result["reason_codes"] = json_load(result.pop("reason_codes_json"), [])
    result["needs_review"] = bool(result["needs_review"])
    latest_review = reviews[0] if reviews else None
    review_selected_ids = json_load(latest_review["selected_transaction_ids_json"], []) if latest_review else []
    result["original_selected_transaction_ids"] = original_selected_ids
    result["selected_transaction_ids"] = review_selected_ids or original_selected_ids
    result = _apply_review_fields(result, latest_review)
    for review in reviews:
        if review:
            review["selected_transaction_ids"] = json_load(review.pop("selected_transaction_ids_json"), [])

    return {
        "result": result,
        "invoice": invoice,
        "bank_transaction": bank,
        "candidates": _bank_rows_by_ids(result["candidate_transaction_ids"]),
        "selected_transactions": _bank_rows_by_ids(result["selected_transaction_ids"]),
        "reviews": reviews,
    }


def _selected_payment_total(selected_ids: list[str]) -> Decimal | None:
    if not selected_ids:
        return None
    total = Decimal("0.00")
    with connect() as conn:
        for tx_id in selected_ids:
            row = row_to_dict(conn.execute("SELECT debit_amount FROM bank_transactions WHERE id = ?", (tx_id,)).fetchone())
            if row:
                total += Decimal(row["debit_amount"] or "0.00")
    return total


def _review_amounts(result: dict, payload: ReviewRequest) -> tuple[Decimal, Decimal]:
    invoice_total: Decimal | None = None
    if result.get("invoice_id"):
        with connect() as conn:
            row = row_to_dict(
                conn.execute("SELECT total_amount FROM invoices WHERE id = ?", (result["invoice_id"],)).fetchone()
            )
        if row and row.get("total_amount") is not None:
            invoice_total = Decimal(row["total_amount"])

    selected_total = _selected_payment_total(payload.selected_transaction_ids)
    matched = payload.matched_amount
    if matched is None:
        matched = selected_total
    if matched is None:
        matched = Decimal(result["matched_amount"] or "0.00")

    if payload.final_status == "unpaid":
        matched = Decimal("0.00")
    elif payload.final_status == "matched" and payload.matched_amount is None and selected_total is None and invoice_total:
        matched = invoice_total

    if payload.outstanding_amount is not None:
        outstanding = payload.outstanding_amount
    elif payload.final_status == "unmatched_bank":
        outstanding = Decimal("0.00")
    elif invoice_total is not None:
        outstanding = max(invoice_total - matched, Decimal("0.00"))
    else:
        outstanding = Decimal(result["outstanding_amount"] or "0.00")

    return matched, outstanding


@app.post("/api/reconciliations/{result_id}/review")
def review_result(result_id: str, payload: ReviewRequest) -> dict[str, str]:
    init_db()
    with connect() as conn:
        result = row_to_dict(conn.execute("SELECT * FROM reconciliation_results WHERE id = ?", (result_id,)).fetchone())
    if not result:
        raise HTTPException(status_code=404, detail="Reconciliation result not found.")

    candidate_ids = set(json_load(result.get("candidate_transaction_ids_json"), []))
    selected_ids = payload.selected_transaction_ids
    if payload.action == "correct" and not selected_ids:
        raise HTTPException(status_code=400, detail="Correcting a match requires at least one selected transaction.")
    if selected_ids and candidate_ids and not set(selected_ids).issubset(candidate_ids):
        raise HTTPException(status_code=400, detail="Selected transaction must be one of the retrieved candidates.")

    matched_amount, outstanding_amount = _review_amounts(result, payload)

    add_review_decision(
        result_id=result_id,
        action=payload.action,
        final_status=payload.final_status,
        selected_transaction_ids=selected_ids,
        final_matched_amount=matched_amount,
        final_outstanding_amount=outstanding_amount,
        note=payload.note,
    )
    return {"message": "Review decision saved."}


@app.post("/api/ask")
def ask(payload: AskRequest) -> dict[str, object]:
    init_db()
    return answer_question(payload.question, payload.result_id)


@app.get("/api/export")
def export_csv() -> StreamingResponse:
    init_db()
    rows = _result_rows()
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "result_id",
            "record_type",
            "vendor_name",
            "invoice_number",
            "invoice_date",
            "invoice_total",
            "suggested_outcome",
            "final_status",
            "matched_amount",
            "outstanding_amount",
            "reason_text",
            "candidate_transaction_ids",
            "selected_transaction_ids",
            "review_action",
            "review_note",
            "bank_description",
            "bank_reference",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "result_id": row["id"],
                "record_type": "invoice" if row.get("invoice_id") else "bank_transaction",
                "vendor_name": row.get("vendor_name"),
                "invoice_number": row.get("invoice_number"),
                "invoice_date": row.get("invoice_date"),
                "invoice_total": row.get("invoice_total"),
                "suggested_outcome": row.get("outcome"),
                "final_status": row.get("final_status"),
                "matched_amount": row.get("matched_amount"),
                "outstanding_amount": row.get("outstanding_amount"),
                "reason_text": row.get("reason_text"),
                "candidate_transaction_ids": "|".join(row.get("candidate_transaction_ids") or []),
                "selected_transaction_ids": "|".join(row.get("selected_transaction_ids") or []),
                "review_action": row.get("review_action"),
                "review_note": row.get("review_note"),
                "bank_description": row.get("bank_description"),
                "bank_reference": row.get("bank_reference"),
            }
        )
    buffer.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="finguard-reconciliation-report.csv"'}
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv", headers=headers)


@app.get("/api/approval/summary")
def approval_summary() -> dict[str, object]:
    init_db()
    init_approval_db()
    rows = approval_rows()
    counts = {status: sum(row["final_status"] == status for row in rows)
              for status in ("approved", "needs_review", "rejected")}
    total = sum((Decimal(row["total_amount"] or "0") for row in rows), Decimal("0"))
    awaiting = sum((Decimal(row["total_amount"] or "0") for row in rows
                    if row["final_status"] == "needs_review"), Decimal("0"))
    return {
        "total_invoices": len(rows), "status_breakdown": counts,
        "total_value": as_money(total), "awaiting_review_value": as_money(awaiting),
        "recent_runs": runs()[:6],
    }


@app.get("/api/approval/vendors")
def approval_vendors() -> dict[str, object]:
    init_db()
    init_approval_db()
    return {"vendors": [item.model_dump(mode="json") for item in vendors()]}


@app.get("/api/approval/purchase-orders")
def approval_pos() -> dict[str, object]:
    init_db()
    init_approval_db()
    prior = approval_rows()
    orders = []
    for po in purchase_orders():
        data = po.model_dump(mode="json")
        consumed = po.opening_invoiced_amount + sum(
            (Decimal(row["total_amount"] or "0") for row in prior
             if row["matched_po"] == po.number and row["final_status"] == "approved"),
            Decimal("0"),
        )
        data["remaining_balance"] = as_money(po.approved_amount - consumed)
        orders.append(data)
    return {"purchase_orders": orders}


@app.post("/api/approval/procurement")
async def upload_procurement(
    vendor_csv: UploadFile | None = File(default=None),
    po_csv: UploadFile | None = File(default=None),
) -> dict[str, object]:
    init_db()
    init_approval_db()
    if not vendor_csv and not po_csv:
        raise HTTPException(status_code=400, detail="Upload a vendor master or PO register CSV.")
    for file in (vendor_csv, po_csv):
        if file and (not (file.filename or "").lower().endswith(".csv")):
            raise HTTPException(status_code=400, detail="Procurement files must be CSV.")
    vendor_bytes = await vendor_csv.read() if vendor_csv else None
    po_bytes = await po_csv.read() if po_csv else None
    if any(len(data) > 4 * 1024 * 1024 for data in (vendor_bytes, po_bytes) if data):
        raise HTTPException(status_code=413, detail="Each procurement CSV must be under 4 MB.")
    try:
        return import_procurement(vendor_bytes, po_bytes)
    except (ValueError, TypeError, InvalidOperation, csv.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/approval/process")
async def start_approval_process(
    background_tasks: BackgroundTasks,
    invoices: list[UploadFile] = File(default=[]),
) -> dict[str, object]:
    init_db()
    init_approval_db()
    if not invoices:
        raise HTTPException(status_code=400, detail="Select at least one invoice PDF.")
    scheduled = []
    for file in invoices:
        filename = Path(file.filename or "invoice.pdf").name
        contents = await file.read()
        if not filename.lower().endswith(".pdf") or len(contents) > 12 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{filename}: expected a PDF no larger than 12 MB.")
        run_id = create_run(filename)
        background_tasks.add_task(process_pdf, run_id, filename, contents)
        scheduled.append({"run_id": run_id, "filename": filename})
    return {"runs": scheduled}


@app.get("/api/approval/runs")
def approval_runs() -> dict[str, object]:
    init_db()
    init_approval_db()
    return {"runs": runs()}


@app.get("/api/approval/runs/{run_id}")
def approval_run_detail(run_id: str) -> dict:
    init_db()
    init_approval_db()
    result = run_detail(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing run not found.")
    return result


@app.get("/api/approval/invoices")
def approval_invoices(search: str = "", status: str = "all") -> dict[str, object]:
    init_db()
    init_approval_db()
    rows = approval_rows()
    if status != "all":
        rows = [row for row in rows if row["final_status"] == status]
    query = search.strip().lower()
    if query:
        rows = [row for row in rows if query in " ".join(str(row.get(key) or "")
                for key in ("vendor_name", "invoice_number", "matched_po", "original_filename")).lower()]
    return {"invoices": rows}


@app.get("/api/approval/invoices/{invoice_id}")
def approval_invoice_detail(invoice_id: str) -> dict:
    init_db()
    init_approval_db()
    result = approval_detail(invoice_id)
    if not result:
        raise HTTPException(status_code=404, detail="Approval invoice not found.")
    return result


@app.post("/api/approval/invoices/{invoice_id}/review")
def review_approval_invoice(invoice_id: str, payload: ApprovalReviewRequest) -> dict[str, str]:
    init_db()
    init_approval_db()
    result = approval_detail(invoice_id)
    if not result:
        raise HTTPException(status_code=404, detail="Approval invoice not found.")
    allowed = {"vendor_name", "gstin", "invoice_number", "invoice_date", "due_date",
               "po_number", "currency", "subtotal", "taxable_amount", "tax_amount",
               "tax_rate", "total_amount", "tax_inclusive", "line_items"}
    if set(payload.corrections) - allowed:
        raise HTTPException(status_code=400, detail="Unsupported corrected field.")
    if payload.selected_po and payload.selected_po not in {po.number for po in purchase_orders()}:
        raise HTTPException(status_code=400, detail="Selected PO is not in the register.")
    try:
        amended = InvoiceFields.model_validate({**result["extraction"], **payload.corrections,
                                                "po_number": payload.selected_po or payload.corrections.get("po_number") or result["extraction"].get("po_number")})
        if amended.total_amount is not None and amended.total_amount <= 0:
            raise ValueError("Invoice total must be positive.")
        if amended.tax_amount is not None and amended.tax_amount < 0:
            raise ValueError("Tax amount cannot be negative.")
        # Validate the human-edited structure without changing the immutable machine suggestion.
        evaluate_invoice(amended, vendors(), purchase_orders(),
                         [p for p in previous_invoices() if p["invoice_id"] != invoice_id])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid correction: {exc}") from exc
    final_status = "approved" if payload.action == "approve" else "rejected"
    add_approval_review(invoice_id, payload.action, final_status, payload.corrections,
                        payload.selected_po or amended.po_number, payload.note,
                        result["suggested_status"])
    previous_corrections = result["reviews"][0]["corrections"] if result["reviews"] else {}
    if payload.corrections or previous_corrections:
        update_fields = {
            "vendor_name": amended.vendor_name,
            "invoice_number": amended.invoice_number,
            "invoice_date": amended.invoice_date.isoformat() if amended.invoice_date else None,
            "total_amount": as_money(amended.total_amount),
            "tax_amount": as_money(amended.tax_amount),
            "currency": amended.currency,
        }
        keys = [key for key in update_fields if key in payload.corrections or key in previous_corrections]
        if keys:
            with connect() as conn:
                conn.execute(f"UPDATE invoices SET {', '.join(f'{key} = ?' for key in keys)} WHERE id = ?",
                             [*(update_fields[key] for key in keys), invoice_id])
            run_reconciliation_and_store()
    return {"message": "Human approval decision saved.", "final_status": final_status}


@app.get("/api/source-files/{source_id}")
def original_source(source_id: str) -> Response:
    init_db()
    init_approval_db()
    item = source_document(source_id)
    if not item or item.get("content") is None:
        raise HTTPException(status_code=404, detail="Original document is unavailable for this record.")
    filename = Path(item["original_filename"]).name.replace('"', "")
    media_type = "application/pdf" if filename.lower().endswith(".pdf") else "text/csv"
    return Response(content=item["content"], media_type=media_type,
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get("/api/source-files/{source_id}/preview")
def source_preview(source_id: str, page: int = 1) -> Response:
    init_db()
    init_approval_db()
    item = source_document(source_id)
    if not item or item.get("content") is None or not item["original_filename"].lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="PDF preview is unavailable.")
    try:
        with fitz.open(stream=item["content"], filetype="pdf") as pdf:
            if page < 1 or page > pdf.page_count:
                raise HTTPException(status_code=400, detail="Page number is outside the document.")
            image = pdf[page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            content = image.tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF preview could not be rendered: {exc}") from exc
    return Response(content=content, media_type="image/png")


@app.get("/api/approval/export")
def approval_export() -> StreamingResponse:
    init_db()
    init_approval_db()
    buffer = io.StringIO()
    keys = ["invoice_id", "vendor", "invoice_number", "po_number", "invoice_amount",
            "po_amount", "difference", "tolerance", "suggested_decision",
            "final_human_decision", "validation_warnings", "reason_for_decision", "review_notes"]
    writer = csv.DictWriter(buffer, fieldnames=keys)
    writer.writeheader()
    po_lookup = {po.number: po for po in purchase_orders()}
    for row in approval_rows():
        detail = approval_detail(row["invoice_id"])
        if not detail:
            continue
        suggestion = detail["suggestion"]
        po = po_lookup.get(detail["selected_po"] or detail["matched_po"])
        writer.writerow({
            "invoice_id": row["invoice_id"], "vendor": row["vendor_name"],
            "invoice_number": row["invoice_number"], "po_number": detail["selected_po"],
            "invoice_amount": row["total_amount"],
            "po_amount": as_money(po.approved_amount) if po else "",
            "difference": suggestion.get("difference"), "tolerance": suggestion.get("tolerance"),
            "suggested_decision": row["suggested_status"],
            "final_human_decision": detail["reviews"][0]["final_status"] if detail["reviews"] else "",
            "validation_warnings": "|".join(suggestion.get("reason_codes") or []),
            "reason_for_decision": suggestion.get("explanation"),
            "review_notes": " | ".join(review["note"] for review in detail["reviews"] if review["note"]),
        })
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="finguard-approval-report.csv"'})

