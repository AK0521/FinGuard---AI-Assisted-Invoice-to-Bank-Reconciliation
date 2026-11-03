from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .database import (
    add_review_decision,
    connect,
    create_source_file,
    fetch_bank_transactions,
    fetch_invoices,
    init_db,
    insert_bank_transactions,
    insert_invoices,
    json_load,
    latest_reviews_by_result,
    reset_workspace,
    row_to_dict,
    run_reconciliation_and_store,
)
from .demo_data import seed_demo_workspace, seed_sample_workspace_if_empty
from .llm import ollama_status
from .normalization import as_money
from .parsing import parse_bank_csv, parse_invoice_pdf
from .retrieval import answer_question


app = FastAPI(title="FinGuard API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()
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
    reset_workspace()
    parsed_invoices = []
    parsed_transactions = []
    messages: list[str] = ["Uploaded files replace the current local workspace."]

    for invoice_file in invoices:
        contents = await invoice_file.read()
        source_id = create_source_file(invoice_file.filename or "invoice.pdf", "invoice_pdf", "uploaded")
        report = parse_invoice_pdf(contents, invoice_file.filename or "invoice.pdf", source_id)
        parsed_invoices.extend(report.invoices)
        messages.extend(report.messages)

    for bank_file in bank_csv:
        contents = await bank_file.read()
        source_id = create_source_file(bank_file.filename or "bank.csv", "bank_csv", "uploaded")
        report = parse_bank_csv(contents, bank_file.filename or "bank.csv", source_id)
        parsed_transactions.extend(report.transactions)
        messages.extend(report.messages)

    if parsed_invoices:
        insert_invoices(parsed_invoices)
    if parsed_transactions:
        insert_bank_transactions(parsed_transactions)
    results = run_reconciliation_and_store()

    return {
        "message": "Upload processed.",
        "messages": messages,
        "invoices": len(parsed_invoices),
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

