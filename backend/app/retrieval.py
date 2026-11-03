from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .database import connect, fetch_bank_transactions, fetch_invoices, insert_evidence_items, json_load, new_id, row_to_dict
from .llm import explain_answer_with_ollama
from .normalization import as_money


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _locator(metadata: dict[str, Any]) -> str:
    if metadata.get("page_number"):
        return f"page {metadata['page_number']}"
    if metadata.get("row_number"):
        return f"CSV row {metadata['row_number']}"
    return metadata.get("locator", "workspace")


def _citation(row: dict[str, Any]) -> dict[str, Any]:
    metadata = json_load(row.get("metadata_json"), {})
    content = row["content"]
    return {
        "source_type": row["source_type"],
        "source_record_id": row["source_record_id"],
        "label": row["title"],
        "locator": _locator(metadata),
        "excerpt": content[:260],
    }


def rebuild_evidence_index() -> None:
    items: list[dict[str, Any]] = []
    for inv in fetch_invoices():
        title = f"Invoice {inv.invoice_number or inv.id} - {inv.vendor_name or 'Unknown vendor'}"
        content = inv.raw_text or " ".join(
            [
                title,
                inv.invoice_date.isoformat() if inv.invoice_date else "date missing",
                as_money(inv.total_amount) or "amount missing",
            ]
        )
        items.append(
            {
                "id": new_id("ev"),
                "source_type": "invoice",
                "source_record_id": inv.id,
                "title": title,
                "content": content,
                "metadata": {
                    "filename": inv.original_filename,
                    "page_number": inv.page_number,
                    "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                },
            }
        )

    for tx in fetch_bank_transactions():
        amount = tx.debit_amount if tx.debit_amount > 0 else tx.credit_amount
        direction = "debit" if tx.debit_amount > 0 else "credit"
        title = f"Bank transaction {tx.reference or tx.id}"
        content = " ".join(
            [
                tx.transaction_date.isoformat() if tx.transaction_date else "date missing",
                tx.description,
                direction,
                as_money(amount) or "0.00",
                tx.reference or "",
            ]
        )
        items.append(
            {
                "id": new_id("ev"),
                "source_type": "bank_transaction",
                "source_record_id": tx.id,
                "title": title,
                "content": content,
                "metadata": {
                    "filename": tx.original_filename,
                    "row_number": tx.row_number,
                    "date": tx.transaction_date.isoformat() if tx.transaction_date else None,
                },
            }
        )

    snippets = [
        (
            "Workspace policy: deterministic status",
            "Workspace policy: payment status is determined only by invoice reference, vendor name, amount, and transaction date window. Retrieval never approves records.",
        ),
        (
            "Workspace policy: human review",
            "Workspace policy: ambiguous duplicate candidates, missing invoice numbers, and amount mismatches require accountant review before final status is trusted.",
        ),
        (
            "Workspace policy: no tax advice",
            "Workspace policy: FinGuard does not claim GST, tax, legal, or banking compliance.",
        ),
    ]
    for title, content in snippets:
        items.append(
            {
                "id": new_id("ev"),
                "source_type": "workspace_policy",
                "source_record_id": title.lower().replace(" ", "-"),
                "title": title,
                "content": content,
                "metadata": {"locator": "workspace policy"},
            }
        )

    insert_evidence_items(items)


def retrieve_evidence(question: str, limit: int = 5, source_record_ids: set[str] | None = None) -> list[dict[str, Any]]:
    query_tokens = _tokens(question)
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM evidence_index").fetchall()]
    scored = []
    for row in rows:
        if source_record_ids and row["source_record_id"] not in source_record_ids:
            continue
        row_tokens = _tokens(f"{row['title']} {row['content']}")
        score = len(query_tokens & row_tokens)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_citation(row) for _, row in scored[:limit]]


def _result_bundle(result_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        result = row_to_dict(conn.execute("SELECT * FROM reconciliation_results WHERE id = ?", (result_id,)).fetchone())
        if not result:
            return None
        invoice = None
        if result.get("invoice_id"):
            invoice = row_to_dict(conn.execute("SELECT * FROM invoices WHERE id = ?", (result["invoice_id"],)).fetchone())
        bank = None
        if result.get("bank_transaction_id"):
            bank = row_to_dict(
                conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (result["bank_transaction_id"],)).fetchone()
            )
        candidate_ids = json_load(result.get("candidate_transaction_ids_json"), [])
        candidates = []
        for tx_id in candidate_ids:
            row = row_to_dict(conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (tx_id,)).fetchone())
            if row:
                candidates.append(row)
    return {"result": result, "invoice": invoice, "bank": bank, "candidates": candidates}


def _money_text(value: str | Decimal | None) -> str:
    if value is None:
        return "INR 0.00"
    return f"INR {value}"


def answer_question(question: str, result_id: str | None = None) -> dict[str, Any]:
    normalized = question.lower().strip()
    if not normalized:
        return _final_answer(question, "Ask a question about the current workspace or the selected reconciliation item.", [])

    if result_id:
        bundle = _result_bundle(result_id)
        if not bundle:
            return _final_answer(question, "The selected reconciliation result was not found.", [])
        result = bundle["result"]
        invoice = bundle["invoice"]
        candidates = bundle["candidates"]
        source_ids = {result["invoice_id"], result.get("bank_transaction_id"), *[row["id"] for row in candidates]}
        source_ids = {source_id for source_id in source_ids if source_id}
        citations = retrieve_evidence(question, source_record_ids=source_ids) or retrieve_evidence(question, limit=3)

        if "evidence" in normalized or "support" in normalized or "why" in normalized or "outstanding" in normalized:
            if invoice:
                inv_label = f"{invoice['vendor_name'] or 'Unknown vendor'} invoice {invoice['invoice_number'] or invoice['id']}"
                answer = (
                    f"{inv_label} is currently classified as {result['outcome'].replace('_', ' ')}. "
                    f"Matched amount is {_money_text(result['matched_amount'])}; outstanding amount is "
                    f"{_money_text(result['outstanding_amount'])}. The deterministic reason is: {result['reason_text']}."
                )
                if candidates:
                    answer += f" Candidate bank transaction count: {len(candidates)}."
                return _final_answer(question, answer, citations)
            if bundle["bank"]:
                tx = bundle["bank"]
                answer = (
                    f"This outgoing bank transaction is classified as unmatched because no invoice matched its "
                    f"reference, vendor text, amount, and date window. Description: {tx['description']}."
                )
                return _final_answer(question, answer, citations)

    matched_result_id = _find_result_for_question(question)
    if matched_result_id:
        bundle = _result_bundle(matched_result_id)
        if bundle and bundle["invoice"]:
            result = bundle["result"]
            invoice = bundle["invoice"]
            candidates = bundle["candidates"]
            source_ids = {result["invoice_id"], *[row["id"] for row in candidates]}
            citations = retrieve_evidence(question, source_record_ids={source_id for source_id in source_ids if source_id})
            answer = (
                f"{invoice['vendor_name'] or 'Unknown vendor'} invoice {invoice['invoice_number'] or invoice['id']} "
                f"is classified as {result['outcome'].replace('_', ' ')}. Matched amount is "
                f"{_money_text(result['matched_amount'])}; outstanding amount is {_money_text(result['outstanding_amount'])}. "
                f"The deterministic reason is: {result['reason_text']}."
            )
            if candidates:
                answer += f" Candidate bank transaction count: {len(candidates)}."
            return _final_answer(question, answer, citations)

    if (
        ("unmatched" in normalized and "bank" in normalized)
        or "no matching invoice" in normalized
        or "without invoice" in normalized
    ):
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT bt.*
                FROM reconciliation_results rr
                JOIN bank_transactions bt ON bt.id = rr.bank_transaction_id
                WHERE rr.outcome = 'unmatched_bank'
                ORDER BY bt.transaction_date, bt.row_number
                """
            ).fetchall()
        if not rows:
            return _final_answer(question, "No unmatched outgoing bank transactions are currently recorded.", [])
        parts = [
            f"{row['transaction_date'] or 'date missing'}: {row['description']} ({_money_text(row['debit_amount'])})"
            for row in rows
        ]
        citations = retrieve_evidence(question, source_record_ids={row["id"] for row in rows})
        return _final_answer(question, "Unmatched outgoing bank transactions: " + "; ".join(parts), citations)

    if any(word in normalized for word in ["total", "balance", "outstanding", "paid"]):
        with connect() as conn:
            rows = conn.execute("SELECT * FROM reconciliation_results WHERE invoice_id IS NOT NULL").fetchall()
        matched = sum(Decimal(row["matched_amount"]) for row in rows)
        outstanding = sum(Decimal(row["outstanding_amount"]) for row in rows)
        needs_review = sum(1 for row in rows if row["needs_review"])
        citations = retrieve_evidence(question, limit=4)
        return _final_answer(
            question,
            (
                f"Structured totals from the current database: matched {_money_text(matched)}, "
                f"outstanding {_money_text(outstanding)}, and {needs_review} invoice item(s) needing review."
            ),
            citations,
        )

    citations = retrieve_evidence(question, limit=5)
    if not citations:
        return _final_answer(question, "The available records do not contain enough evidence to answer that question.", [])
    return _final_answer(
        question,
        "I found related source evidence in the current workspace. The deterministic reconciliation engine remains the source of truth for status and amounts.",
        citations,
    )


def _find_result_for_question(question: str) -> str | None:
    query_tokens = _tokens(question)
    if not query_tokens:
        return None
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rr.id, inv.vendor_name, inv.invoice_number
            FROM reconciliation_results rr
            JOIN invoices inv ON inv.id = rr.invoice_id
            """
        ).fetchall()
    best_score = 0
    best_id = None
    for row in rows:
        row_tokens = _tokens(f"{row['vendor_name'] or ''} {row['invoice_number'] or ''}")
        score = len(query_tokens & row_tokens)
        if score > best_score:
            best_score = score
            best_id = row["id"]
    return best_id if best_score >= 1 else None


def _final_answer(question: str, answer: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    enhanced = explain_answer_with_ollama(question, answer, citations)
    return {
        "answer": enhanced or answer,
        "citations": citations,
        "answer_mode": "ollama_rag" if enhanced else "deterministic_rag",
    }

