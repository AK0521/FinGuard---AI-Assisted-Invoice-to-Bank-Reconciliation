"""SQLite persistence for procurement, approval runs, evidence and human decisions."""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from .approval import ApprovalDecision, InvoiceFields, LineItem, PurchaseOrder, Vendor
from .database import connect, create_source_file, json_dump, json_load, new_id, row_to_dict, utc_now
from .normalization import as_money


def init_approval_db() -> None:
    with connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_files)")}
        if "content" not in columns:
            conn.execute("ALTER TABLE source_files ADD COLUMN content BLOB")
        if "sha256" not in columns:
            conn.execute("ALTER TABLE source_files ADD COLUMN sha256 TEXT")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vendors (
                id TEXT PRIMARY KEY, legal_name TEXT NOT NULL, gstin TEXT,
                approved INTEGER NOT NULL, active INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS purchase_orders (
                number TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, po_date TEXT,
                status TEXT NOT NULL, currency TEXT NOT NULL, approved_amount TEXT NOT NULL,
                opening_invoiced_amount TEXT NOT NULL, tolerance_percent TEXT NOT NULL,
                tolerance_amount TEXT NOT NULL, tax_treatment TEXT NOT NULL, tax_rate TEXT,
                line_items_json TEXT NOT NULL,
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            );
            CREATE TABLE IF NOT EXISTS approval_runs (
                id TEXT PRIMARY KEY, invoice_id TEXT, filename TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS approval_events (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, invoice_id TEXT,
                stage TEXT NOT NULL, status TEXT NOT NULL, explanation TEXT NOT NULL,
                output_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES approval_runs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS approvals (
                invoice_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_sha256 TEXT,
                extraction_method TEXT NOT NULL, extraction_json TEXT NOT NULL,
                suggestion_json TEXT NOT NULL, suggested_status TEXT NOT NULL,
                matched_po TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES approval_runs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS approval_reviews (
                id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, action TEXT NOT NULL,
                final_status TEXT NOT NULL, corrections_json TEXT NOT NULL,
                selected_po TEXT, note TEXT, previous_suggestion TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_approval_events_run ON approval_events(run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_approval_reviews_invoice ON approval_reviews(invoice_id, created_at);
        """)


def reset_approval_data() -> None:
    with connect() as conn:
        for table in ("approval_reviews", "approvals", "approval_events", "approval_runs", "purchase_orders", "vendors"):
            conn.execute(f"DELETE FROM {table}")


def save_source(filename: str, contents: bytes, kind: str) -> tuple[str, str]:
    sha = hashlib.sha256(contents).hexdigest()
    source_id = create_source_file(filename, kind, "processing")
    with connect() as conn:
        conn.execute("UPDATE source_files SET content = ?, sha256 = ? WHERE id = ?", (contents, sha, source_id))
    return source_id, sha


def source_document(source_id: str) -> dict | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM source_files WHERE id = ?", (source_id,)).fetchone())


def set_source_status(source_id: str, status: str, message: str | None = None) -> None:
    with connect() as conn:
        conn.execute("UPDATE source_files SET status = ?, message = ? WHERE id = ?", (status, message, source_id))


def upsert_procurement(vendors: list[Vendor], pos: list[PurchaseOrder]) -> None:
    with connect() as conn:
        for vendor in vendors:
            conn.execute("""INSERT INTO vendors (id, legal_name, gstin, approved, active)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
                legal_name=excluded.legal_name, gstin=excluded.gstin,
                approved=excluded.approved, active=excluded.active""",
                (vendor.id, vendor.legal_name, vendor.gstin, int(vendor.approved), int(vendor.active)))
        for po in pos:
            conn.execute("""INSERT INTO purchase_orders
                (number, vendor_id, po_date, status, currency, approved_amount,
                 opening_invoiced_amount, tolerance_percent, tolerance_amount,
                 tax_treatment, tax_rate, line_items_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(number) DO UPDATE SET
                vendor_id=excluded.vendor_id, po_date=excluded.po_date, status=excluded.status,
                currency=excluded.currency, approved_amount=excluded.approved_amount,
                opening_invoiced_amount=excluded.opening_invoiced_amount,
                tolerance_percent=excluded.tolerance_percent, tolerance_amount=excluded.tolerance_amount,
                tax_treatment=excluded.tax_treatment, tax_rate=excluded.tax_rate,
                line_items_json=excluded.line_items_json""",
                (po.number, po.vendor_id, po.po_date.isoformat() if po.po_date else None,
                 po.status, po.currency, as_money(po.approved_amount),
                 as_money(po.opening_invoiced_amount), as_money(po.tolerance_percent),
                 as_money(po.tolerance_amount), po.tax_treatment, as_money(po.tax_rate),
                 json_dump([item.model_dump(mode="json") for item in po.line_items])))


def vendors() -> list[Vendor]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM vendors ORDER BY legal_name").fetchall()
    return [Vendor(id=r["id"], legal_name=r["legal_name"], gstin=r["gstin"], approved=bool(r["approved"]), active=bool(r["active"])) for r in rows]


def purchase_orders() -> list[PurchaseOrder]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM purchase_orders ORDER BY number").fetchall()
    return [PurchaseOrder(
        number=r["number"], vendor_id=r["vendor_id"], po_date=r["po_date"], status=r["status"],
        currency=r["currency"], approved_amount=Decimal(r["approved_amount"]),
        opening_invoiced_amount=Decimal(r["opening_invoiced_amount"]),
        tolerance_percent=Decimal(r["tolerance_percent"]), tolerance_amount=Decimal(r["tolerance_amount"]),
        tax_treatment=r["tax_treatment"], tax_rate=Decimal(r["tax_rate"]) if r["tax_rate"] else None,
        line_items=[LineItem.model_validate(item) for item in json_load(r["line_items_json"], [])],
    ) for r in rows]


def create_run(filename: str) -> str:
    run_id = new_id("run")
    with connect() as conn:
        conn.execute("INSERT INTO approval_runs (id, filename, status, created_at) VALUES (?, ?, ?, ?)",
                     (run_id, filename, "pending", utc_now()))
    return run_id


def event(run_id: str, stage: str, status: str, explanation: str, output: dict | None = None, invoice_id: str | None = None) -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO approval_events
            (id, run_id, invoice_id, stage, status, explanation, output_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id("evt"), run_id, invoice_id, stage, status, explanation, json_dump(output or {}), utc_now()))


def update_run(run_id: str, status: str, *, invoice_id: str | None = None, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute("""UPDATE approval_runs SET status = ?, invoice_id = COALESCE(?, invoice_id),
            error = ?, finished_at = CASE WHEN ? IN ('completed', 'failed') THEN ? ELSE NULL END
            WHERE id = ?""", (status, invoice_id, error, status, utc_now(), run_id))


def save_approval(invoice_id: str, run_id: str, sha: str, method: str, fields: InvoiceFields, decision: ApprovalDecision) -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO approvals
            (invoice_id, run_id, source_sha256, extraction_method, extraction_json,
             suggestion_json, suggested_status, matched_po, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (invoice_id, run_id, sha, method, json_dump(fields.model_dump(mode="json")),
             json_dump(decision.model_dump(mode="json")), decision.suggested_status,
             decision.matched_po, utc_now()))


def previous_invoices() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("""SELECT a.invoice_id, a.source_sha256,
            COALESCE((SELECT ar.selected_po FROM approval_reviews ar WHERE ar.invoice_id = a.invoice_id
                      ORDER BY ar.rowid DESC LIMIT 1), a.matched_po) AS matched_po,
            a.suggested_status, i.vendor_name, i.invoice_number, i.invoice_date, i.total_amount,
            (SELECT ar.final_status FROM approval_reviews ar WHERE ar.invoice_id = a.invoice_id
             ORDER BY ar.created_at DESC, ar.rowid DESC LIMIT 1) AS human_status
            FROM approvals a JOIN invoices i ON i.id = a.invoice_id ORDER BY a.created_at, a.rowid""").fetchall()
    result = []
    for row in rows:
        item = row_to_dict(row) or {}
        item["effective_status"] = item["human_status"] or item["suggested_status"]
        result.append(item)
    return result


def latest_approval_reviews() -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute("""SELECT * FROM approval_reviews WHERE rowid IN
            (SELECT MAX(rowid) FROM approval_reviews GROUP BY invoice_id)""").fetchall()
    return {r["invoice_id"]: row_to_dict(r) or {} for r in rows}


def approval_rows() -> list[dict]:
    reviews = latest_approval_reviews()
    with connect() as conn:
        rows = conn.execute("""SELECT a.*, i.vendor_name, i.invoice_number, i.invoice_date,
            i.total_amount, i.original_filename, i.source_file_id, r.status AS run_status
            FROM approvals a JOIN invoices i ON i.id = a.invoice_id
            JOIN approval_runs r ON r.id = a.run_id
            ORDER BY a.created_at DESC, a.rowid DESC""").fetchall()
    result = []
    for row in rows:
        data = row_to_dict(row) or {}
        review = reviews.get(data["invoice_id"])
        data["final_status"] = review["final_status"] if review else data["suggested_status"]
        data["matched_po"] = review["selected_po"] or data["matched_po"] if review else data["matched_po"]
        data["reason_codes"] = json_load(data["suggestion_json"], {}).get("reason_codes", [])
        data.pop("extraction_json", None)
        data.pop("suggestion_json", None)
        result.append(data)
    return result


def approval_detail(invoice_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("""SELECT a.*, i.vendor_name, i.invoice_number, i.invoice_date,
            i.total_amount, i.original_filename, i.source_file_id, i.raw_text, i.flags_json
            FROM approvals a JOIN invoices i ON i.id = a.invoice_id WHERE a.invoice_id = ?""", (invoice_id,)).fetchone()
        if not row:
            return None
        review_rows = conn.execute("SELECT * FROM approval_reviews WHERE invoice_id = ? ORDER BY rowid DESC", (invoice_id,)).fetchall()
        payment = conn.execute("SELECT * FROM reconciliation_results WHERE invoice_id = ?", (invoice_id,)).fetchone()
        events = conn.execute("SELECT * FROM approval_events WHERE run_id = ? ORDER BY rowid", (row["run_id"],)).fetchall()
    data = row_to_dict(row) or {}
    data["extraction"] = json_load(data.pop("extraction_json"), {})
    data["suggestion"] = json_load(data.pop("suggestion_json"), {})
    data["flags"] = json_load(data.pop("flags_json"), [])
    data["reviews"] = []
    for r in review_rows:
        item = row_to_dict(r) or {}
        item["corrections"] = json_load(item.pop("corrections_json"), {})
        data["reviews"].append(item)
    data["final_status"] = data["reviews"][0]["final_status"] if data["reviews"] else data["suggested_status"]
    data["selected_po"] = data["reviews"][0]["selected_po"] if data["reviews"] else data["matched_po"]
    data["payment"] = row_to_dict(payment)
    data["events"] = [dict(row_to_dict(e) or {}, output=json_load(e["output_json"], {})) for e in events]
    for item in data["events"]:
        item.pop("output_json", None)
    return data


def add_approval_review(invoice_id: str, action: str, final_status: str, corrections: dict,
                        selected_po: str | None, note: str, previous_suggestion: str) -> str:
    review_id = new_id("aprev")
    with connect() as conn:
        conn.execute("""INSERT INTO approval_reviews
            (id, invoice_id, action, final_status, corrections_json, selected_po, note, previous_suggestion, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (review_id, invoice_id, action, final_status, json_dump(corrections), selected_po,
             note, previous_suggestion, utc_now()))
    return review_id


def run_detail(run_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM approval_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        events = conn.execute("SELECT * FROM approval_events WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
    data = row_to_dict(row) or {}
    data["events"] = []
    for e in events:
        item = row_to_dict(e) or {}
        item["output"] = json_load(item.pop("output_json"), {})
        data["events"].append(item)
    return data


def runs() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("""SELECT r.*, a.suggested_status,
            (SELECT ar.final_status FROM approval_reviews ar WHERE ar.invoice_id = r.invoice_id
             ORDER BY ar.rowid DESC LIMIT 1) AS human_status
            FROM approval_runs r LEFT JOIN approvals a ON a.run_id = r.id
            ORDER BY r.created_at DESC, r.rowid DESC""").fetchall()
    result = [row_to_dict(r) or {} for r in rows]
    for item in result:
        item["final_status"] = item.pop("human_status") or item["suggested_status"]
    return result
