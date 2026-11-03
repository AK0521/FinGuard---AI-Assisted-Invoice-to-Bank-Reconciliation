from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .models import BankTransactionRecord, InvoiceRecord, ReconciliationSuggestion
from .normalization import as_money, money, parse_date


WORKSPACE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = WORKSPACE_DIR / "data"


def db_path() -> Path:
    return Path(os.environ.get("FINGUARD_DB", str(DATA_DIR / "finguard.sqlite3")))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_files (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                source_file_id TEXT,
                original_filename TEXT,
                vendor_name TEXT,
                invoice_number TEXT,
                invoice_date TEXT,
                total_amount TEXT,
                tax_amount TEXT,
                currency TEXT NOT NULL DEFAULT 'INR',
                page_number INTEGER,
                raw_text TEXT NOT NULL DEFAULT '',
                flags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_file_id) REFERENCES source_files(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS bank_transactions (
                id TEXT PRIMARY KEY,
                source_file_id TEXT,
                original_filename TEXT,
                transaction_date TEXT,
                description TEXT NOT NULL,
                debit_amount TEXT NOT NULL,
                credit_amount TEXT NOT NULL,
                reference TEXT,
                row_number INTEGER,
                flags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_file_id) REFERENCES source_files(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS reconciliation_results (
                id TEXT PRIMARY KEY,
                invoice_id TEXT,
                bank_transaction_id TEXT,
                outcome TEXT NOT NULL,
                matched_amount TEXT NOT NULL,
                outstanding_amount TEXT NOT NULL,
                candidate_transaction_ids_json TEXT NOT NULL DEFAULT '[]',
                selected_transaction_ids_json TEXT NOT NULL DEFAULT '[]',
                reason_codes_json TEXT NOT NULL DEFAULT '[]',
                reason_text TEXT NOT NULL,
                confidence TEXT NOT NULL,
                needs_review INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                FOREIGN KEY (bank_transaction_id) REFERENCES bank_transactions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_decisions (
                id TEXT PRIMARY KEY,
                result_id TEXT NOT NULL,
                action TEXT NOT NULL,
                final_status TEXT NOT NULL,
                selected_transaction_ids_json TEXT NOT NULL DEFAULT '[]',
                final_matched_amount TEXT,
                final_outstanding_amount TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (result_id) REFERENCES reconciliation_results(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evidence_index (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_column(conn, "review_decisions", "final_matched_amount", "TEXT")
        _ensure_column(conn, "review_decisions", "final_outstanding_amount", "TEXT")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def reset_workspace() -> None:
    with connect() as conn:
        for table in [
            "evidence_index",
            "review_decisions",
            "reconciliation_results",
            "bank_transactions",
            "invoices",
            "source_files",
        ]:
            conn.execute(f"DELETE FROM {table}")


def json_dump(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=True)


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def create_source_file(original_filename: str, source_type: str, status: str = "processed", message: str | None = None) -> str:
    source_id = new_id("src")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files (id, original_filename, source_type, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, original_filename, source_type, status, message, utc_now()),
        )
    return source_id


def insert_invoices(invoices: list[InvoiceRecord]) -> None:
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO invoices (
                id, source_file_id, original_filename, vendor_name, invoice_number, invoice_date,
                total_amount, tax_amount, currency, page_number, raw_text, flags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    inv.id,
                    inv.source_file_id,
                    inv.original_filename,
                    inv.vendor_name,
                    inv.invoice_number,
                    inv.invoice_date.isoformat() if inv.invoice_date else None,
                    as_money(inv.total_amount),
                    as_money(inv.tax_amount),
                    inv.currency,
                    inv.page_number,
                    inv.raw_text,
                    json_dump(inv.flags),
                    utc_now(),
                )
                for inv in invoices
            ],
        )


def insert_bank_transactions(transactions: list[BankTransactionRecord]) -> None:
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO bank_transactions (
                id, source_file_id, original_filename, transaction_date, description, debit_amount,
                credit_amount, reference, row_number, flags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    tx.id,
                    tx.source_file_id,
                    tx.original_filename,
                    tx.transaction_date.isoformat() if tx.transaction_date else None,
                    tx.description,
                    as_money(tx.debit_amount) or "0.00",
                    as_money(tx.credit_amount) or "0.00",
                    tx.reference,
                    tx.row_number,
                    json_dump(tx.flags),
                    utc_now(),
                )
                for tx in transactions
            ],
        )


def fetch_invoices() -> list[InvoiceRecord]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM invoices ORDER BY invoice_date, vendor_name").fetchall()
    return [
        InvoiceRecord(
            id=row["id"],
            source_file_id=row["source_file_id"],
            original_filename=row["original_filename"],
            vendor_name=row["vendor_name"],
            invoice_number=row["invoice_number"],
            invoice_date=parse_date(row["invoice_date"]),
            total_amount=money(row["total_amount"]),
            tax_amount=money(row["tax_amount"]),
            currency=row["currency"] or "INR",
            page_number=row["page_number"],
            raw_text=row["raw_text"] or "",
            flags=json_load(row["flags_json"], []),
        )
        for row in rows
    ]


def fetch_bank_transactions() -> list[BankTransactionRecord]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM bank_transactions ORDER BY transaction_date, row_number").fetchall()
    return [
        BankTransactionRecord(
            id=row["id"],
            source_file_id=row["source_file_id"],
            original_filename=row["original_filename"],
            transaction_date=parse_date(row["transaction_date"]),
            description=row["description"],
            debit_amount=money(row["debit_amount"]) or Decimal("0.00"),
            credit_amount=money(row["credit_amount"]) or Decimal("0.00"),
            reference=row["reference"],
            row_number=row["row_number"],
            flags=json_load(row["flags_json"], []),
        )
        for row in rows
    ]


def replace_reconciliation_results(results: list[ReconciliationSuggestion]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM review_decisions")
        conn.execute("DELETE FROM reconciliation_results")
        conn.executemany(
            """
            INSERT INTO reconciliation_results (
                id, invoice_id, bank_transaction_id, outcome, matched_amount, outstanding_amount,
                candidate_transaction_ids_json, selected_transaction_ids_json, reason_codes_json,
                reason_text, confidence, needs_review, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    result.id,
                    result.invoice_id,
                    result.bank_transaction_id,
                    result.outcome,
                    as_money(result.matched_amount) or "0.00",
                    as_money(result.outstanding_amount) or "0.00",
                    json_dump(result.candidate_transaction_ids),
                    json_dump(result.selected_transaction_ids),
                    json_dump(result.reason_codes),
                    result.reason_text,
                    as_money(result.confidence) or "0.00",
                    1 if result.needs_review else 0,
                    utc_now(),
                )
                for result in results
            ],
        )


def run_reconciliation_and_store() -> list[ReconciliationSuggestion]:
    from .reconciliation import reconcile_records
    from .retrieval import rebuild_evidence_index

    results = reconcile_records(fetch_invoices(), fetch_bank_transactions())
    replace_reconciliation_results(results)
    rebuild_evidence_index()
    return results


def latest_reviews_by_result() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rd.*
            FROM review_decisions rd
            JOIN (
                SELECT result_id, MAX(created_at) AS created_at
                FROM review_decisions
                GROUP BY result_id
            ) latest
              ON latest.result_id = rd.result_id AND latest.created_at = rd.created_at
            """
        ).fetchall()
    return {row["result_id"]: row_to_dict(row) or {} for row in rows}


def add_review_decision(
    result_id: str,
    action: str,
    final_status: str,
    selected_transaction_ids: list[str],
    note: str | None = None,
    final_matched_amount: Decimal | None = None,
    final_outstanding_amount: Decimal | None = None,
) -> str:
    review_id = new_id("rev")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO review_decisions (
                id, result_id, action, final_status, selected_transaction_ids_json,
                final_matched_amount, final_outstanding_amount, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                result_id,
                action,
                final_status,
                json_dump(selected_transaction_ids),
                as_money(final_matched_amount),
                as_money(final_outstanding_amount),
                note,
                utc_now(),
            ),
        )
    return review_id


def insert_evidence_items(items: list[dict[str, Any]]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM evidence_index")
        conn.executemany(
            """
            INSERT INTO evidence_index (
                id, source_type, source_record_id, title, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["id"],
                    item["source_type"],
                    item["source_record_id"],
                    item["title"],
                    item["content"],
                    json_dump(item.get("metadata", {})),
                    utc_now(),
                )
                for item in items
            ],
        )

