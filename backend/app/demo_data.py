"""Small initial demo plus a source-backed catalog for live uploads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .approval_pipeline import import_procurement, process_pdf
from .approval_store import create_run, init_approval_db
from .database import connect, reset_workspace, run_reconciliation_and_store


SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"


def demo_manifest() -> dict:
    manifest = json.loads((SAMPLES / "demo_manifest.json").read_text(encoding="utf-8"))
    listed = {entry["path"] for entry in manifest["invoices"]}
    initial = manifest["initial_invoices"]
    if not 2 <= len(initial) <= 3 or len(initial) != len(set(initial)) or not set(initial).issubset(listed):
        raise ValueError("Demo manifest must name two or three distinct sample invoices.")
    for relative in [*listed, manifest["bank_statement"]["path"]]:
        path = (SAMPLES / relative).resolve()
        if not path.is_relative_to(SAMPLES.resolve()) or not path.is_file():
            raise FileNotFoundError(f"Demo sample missing: {relative}")
    return manifest


def sample_catalog() -> dict:
    """Read the committed files and current DB state; never invent a document row."""
    manifest = demo_manifest()
    with connect() as conn:
        processed_invoices = {row["source_sha256"] for row in conn.execute(
            "SELECT source_sha256 FROM approvals WHERE source_sha256 IS NOT NULL"
        )}
        imported_bank = {row["sha256"] for row in conn.execute("""
            SELECT DISTINCT sf.sha256 FROM source_files sf
            JOIN bank_transactions bt ON bt.source_file_id = sf.id
            WHERE sf.source_type = 'bank_csv' AND sf.status = 'processed'
        """)}
    initial = set(manifest["initial_invoices"])
    invoices = []
    for entry in manifest["invoices"]:
        path = SAMPLES / entry["path"]
        invoices.append({
            "filename": path.name,
            "folder": "sample_data/live_upload" if path.parent != SAMPLES else "sample_data",
            "title": entry["title"],
            "scenario": entry["scenario"],
            "preloaded": path.name in initial,
            "processed": hashlib.sha256(path.read_bytes()).hexdigest() in processed_invoices,
            "upload_page": "Invoice approval",
        })
    bank_entry = manifest["bank_statement"]
    bank_path = SAMPLES / bank_entry["path"]
    bank = {
        "filename": bank_path.name,
        "folder": "sample_data",
        "title": bank_entry["title"],
        "scenario": bank_entry["scenario"],
        "processed": hashlib.sha256(bank_path.read_bytes()).hexdigest() in imported_bank,
        "upload_page": "Payment reconciliation",
    }
    return {"invoices": invoices, "bank_statement": bank}


def seed_sample_workspace_if_empty() -> dict[str, int | str]:
    with connect() as conn:
        counts = conn.execute("SELECT COUNT(*) AS invoices FROM invoices").fetchone()
    if counts and counts["invoices"]:
        return {"message": "Existing workspace retained.", "invoices": counts["invoices"]}
    return seed_demo_workspace()


def seed_demo_workspace() -> dict[str, int | str]:
    manifest = demo_manifest()
    init_approval_db()
    reset_workspace()
    imported = import_procurement((SAMPLES / "vendors.csv").read_bytes(),
                                  (SAMPLES / "purchase_orders.csv").read_bytes())
    completed = 0
    for filename in manifest["initial_invoices"]:
        path = SAMPLES / filename
        run_id = create_run(path.name)
        process_pdf(run_id, path.name, path.read_bytes())
        with connect() as conn:
            row = conn.execute("SELECT status FROM approval_runs WHERE id = ?", (run_id,)).fetchone()
        completed += bool(row and row["status"] == "completed")
    results = run_reconciliation_and_store()
    return {
        "message": f"Demo workspace restored: {completed} invoices processed. Other PDFs and the bank CSV are ready for live upload.",
        "invoices": completed,
        "bank_transactions": 0,
        "reconciliation_results": len(results),
        "vendors": imported["vendors"],
        "purchase_orders": imported["purchase_orders"],
    }
