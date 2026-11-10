"""Regenerate the fictional procurement and invoice evidence shipped with FinGuard."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_data"

VENDORS = [
    ("V-METRO", "Metro Facility Services", "29METRO1234A1Z5", "yes", "yes"),
    ("V-KAVERI", "Kaveri Paper Mart", "29KAVERI123A1Z5", "yes", "yes"),
    ("V-PIXEL", "PixelCraft Design Studio", "29PIXEL1234A1Z5", "yes", "yes"),
    ("V-BLUE", "Blue Lotus Packaging", "29BLUE12345A1Z5", "yes", "yes"),
    ("V-CEDAR", "Cedar Cleaning Care", "29CEDAR1234A1Z5", "yes", "yes"),
    ("V-NARMADA", "Narmada Office Tech", "29NARMADA12A1Z5", "no", "yes"),
    ("V-RIVER", "Riverbend Logistics", "29RIVER1234A1Z5", "yes", "no"),
]

POS = [
    ("PO-FAC-101", "V-METRO", "2026-01-05", "open", "INR", "11800", "0", "0", "0", "exclusive", "18", [{"description": "Monthly facility maintenance", "quantity": "1", "unit_price": "10000", "line_total": "10000"}]),
    ("PO-STAT-201", "V-KAVERI", "2026-01-05", "open", "INR", "42000", "0", "1", "0", "exclusive", "18", [{"description": "Copier paper and stationery bulk order", "quantity": "1", "unit_price": "35000", "line_total": "35000"}]),
    ("PO-DES-301", "V-PIXEL", "2026-01-08", "open", "INR", "32500", "0", "0", "0", "exclusive", "18", [{"description": "Dashboard prototype design sprint", "quantity": "1", "unit_price": "27542.37", "line_total": "27542.37"}]),
    ("PO-PACK-401", "V-BLUE", "2026-01-30", "open", "INR", "30000", "0", "0", "0", "exclusive", "18", [{"description": "Corrugated cartons", "quantity": "3", "unit_price": "8474.58", "line_total": "25423.74"}]),
    ("PO-CLEAN-501", "V-CEDAR", "2026-02-01", "open", "INR", "9500", "0", "0", "0", "exclusive", "18", [{"description": "Deep cleaning support", "quantity": "1", "unit_price": "8050.85", "line_total": "8050.85"}]),
    ("PO-TECH-601", "V-NARMADA", "2026-02-02", "open", "INR", "25000", "0", "2", "0", "exclusive", "18", [{"description": "Office networking", "quantity": "1", "unit_price": "21186.44", "line_total": "21186.44"}]),
    ("PO-LOG-701", "V-RIVER", "2026-02-03", "closed", "INR", "18000", "0", "0", "0", "exclusive", "18", [{"description": "Freight handling", "quantity": "1", "unit_price": "15254.24", "line_total": "15254.24"}]),
    ("PO-PACK-402", "V-BLUE", "2026-03-01", "open", "INR", "16000", "0", "0", "0", "inclusive", "18", [{"description": "Protective sleeves", "quantity": "1", "unit_price": "13559.32", "line_total": "13559.32"}]),
]

INVOICES = [
    dict(filename="01_metro_clean.pdf", vendor="Metro Facility Services", number="MFS-908", date="16 Jan 2026", po="PO-FAC-101", subtotal="10000.00", tax="1800.00", total="11800.00", desc="Monthly facility maintenance", unit="10000.00", layout=0, gstin="29METRO1234A1Z5"),
    dict(filename="02_kaveri_tax_discrepancy.pdf", vendor="Kaveri Paper Mart", number="KPM-331", date="12 Jan 2026", po="PO-STAT-201", subtotal="35000.00", tax="7000.00", total="42000.00", desc="Copier paper and stationery bulk order", unit="35000.00", layout=1, gstin="29KAVERI123A1Z5"),
    dict(filename="03_pixelcraft_bundled.pdf", vendor="PixelCraft Design Studio", number="PCD-2026-009", date="14 Jan 2026", po="PO-DES-301", subtotal="27542.37", tax="4957.63", total="32500.00", desc="Dashboard prototype design sprint", unit="27542.37", layout=2, gstin="29PIXEL1234A1Z5"),
    dict(filename="04_blue_lotus_split_one.pdf", vendor="Blue Lotus Packaging", number="BLP-9001", date="05 Feb 2026", po="PO-PACK-401", subtotal="10169.49", tax="1830.51", total="12000.00", desc="Corrugated cartons", unit="10169.49", layout=0, gstin="29BLUE12345A1Z5"),
    dict(filename="05_blue_lotus_split_two.pdf", vendor="Blue Lotus Packaging", number="BLP-9002", date="10 Feb 2026", po="PO-PACK-401", subtotal="10169.49", tax="1830.51", total="12000.00", desc="Corrugated cartons", unit="10169.49", layout=1, gstin="29BLUE12345A1Z5"),
    dict(filename="06_blue_lotus_over_balance.pdf", vendor="Blue Lotus Packaging", number="BLP-9003", date="14 Feb 2026", po="PO-PACK-401", subtotal="8474.58", tax="1525.42", total="10000.00", desc="Corrugated cartons", unit="8474.58", layout=2, gstin="29BLUE12345A1Z5"),
    dict(filename="07_metro_duplicate.pdf", vendor="Metro Facility Services", number="MFS-908", date="16 Jan 2026", po="PO-FAC-101", subtotal="10000.00", tax="1800.00", total="11800.00", desc="Monthly facility maintenance", unit="10000.00", layout=2, gstin="29METRO1234A1Z5"),
    dict(filename="08_cedar_scanned_missing_number.pdf", vendor="Cedar Cleaning Care", number="", date="08 Feb 2026", po="PO-CLEAN-501", subtotal="8050.85", tax="1449.15", total="9500.00", desc="Deep cleaning support", unit="8050.85", layout=3, gstin="29CEDAR1234A1Z5"),
]


def write_csv(path: Path, headers: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def make_invoice(spec: dict, directory: Path = OUT) -> None:
    path = directory / spec["filename"]
    if spec["layout"] == 3:
        make_scanned_invoice(path, spec)
        return
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    ink = (0.08, 0.15, 0.25)
    teal = (0.03, 0.45, 0.44)
    gray = (0.4, 0.46, 0.52)
    page.draw_rect(fitz.Rect(0, 0, 595, 12), color=teal, fill=teal)
    page.insert_text((48, 72), spec["vendor"], fontsize=20, fontname="hebo", color=ink)
    page.insert_text((49, 94), "Bengaluru, Karnataka 560001  |  accounts@example.invalid", fontsize=8, color=gray)
    page.insert_text((49, 112), "GSTIN: " + spec["gstin"], fontsize=9, color=gray)
    title = "TAX INVOICE" if spec["layout"] != 2 else "INVOICE / SERVICES"
    page.insert_text((48, 160), title, fontsize=18, fontname="hebo", color=teal)
    page.draw_line((48, 174), (547, 174), color=(0.8, 0.85, 0.88), width=1)
    number_label = "Invoice Number" if spec["layout"] != 2 else "Invoice No"
    fields = [
        f"{number_label}: {spec['number']}", f"Invoice Date: {spec['date']}",
        f"PO Number: {spec['po']}", "Currency: INR", "Bill To: FinGuard Evaluation Lab",
    ]
    y = 205
    for line in fields:
        if not line.endswith(": "):
            page.insert_text((50, y), line, fontsize=10, color=ink)
        y += 23
    page.draw_rect(fitz.Rect(48, 338, 547, 373), color=(0.89, 0.93, 0.94), fill=(0.93, 0.96, 0.96))
    page.insert_text((60, 360), "DESCRIPTION / QUANTITY / UNIT / VALUE", fontsize=9, fontname="hebo", color=teal)
    # One coherent line gives both the visual row and a reliable text extraction path.
    page.insert_text((59, 403), f"Item: {spec['desc']} | Qty: 1 | Unit: {spec['unit']} | Amount: {spec['subtotal']}", fontsize=8.6, color=ink)
    page.draw_line((48, 424), (547, 424), color=(0.83, 0.87, 0.89), width=1)
    totals = [("Subtotal", spec["subtotal"]), ("GST", spec["tax"]), ("Grand Total", spec["total"])]
    y = 465
    for label, value in totals:
        page.insert_text((356, y), f"{label}: INR {value}", fontsize=11 if label == "Grand Total" else 10,
                         fontname="hebo" if label == "Grand Total" else "helv", color=teal if label == "Grand Total" else ink)
        y += 26
    if spec.get("tax_note"):
        page.insert_text((49, 568), spec["tax_note"], fontsize=9, color=gray)
    page.insert_text((49, 665), "Payment terms: Net 15 days. Please quote the invoice number in remittance advice.", fontsize=8, color=gray)
    page.insert_text((49, 718), "Authorised digital invoice  |  Generated for FinGuard evaluation", fontsize=8, color=gray)
    doc.save(path)
    doc.close()


def _font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def make_scanned_invoice(path: Path, spec: dict) -> None:
    image = Image.new("RGB", (1240, 1754), "#faf9f6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 70, 1170, 1660), outline="#245b63", width=4)
    draw.rectangle((70, 70, 1170, 180), fill="#123448")
    draw.text((115, 104), spec["vendor"], font=_font(43), fill="white")
    lines = ["TAX INVOICE", f"Invoice Date: {spec['date']}", f"PO Number: {spec['po']}",
             "Currency: INR", "Bill To: FinGuard Evaluation Lab", f"GSTIN: {spec['gstin']}",
             f"Description: {spec['desc']}", f"Subtotal: INR {spec['subtotal']}",
             f"GST: INR {spec['tax']}", f"Grand Total: INR {spec['total']}"]
    y = 250
    for idx, line in enumerate(lines):
        draw.text((115, y), line, font=_font(39 if idx == 0 else 30), fill="#203245")
        y += 82 if idx == 0 else 74
    draw.text((115, 1480), "Invoice reference unavailable in source scan", font=_font(28), fill="#596a75")
    image.save(path, "PDF", resolution=180.0)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    write_csv(OUT / "vendors.csv", ["vendor_id", "legal_name", "gstin", "approved", "active"], VENDORS)
    po_rows = [(*row[:-1], json.dumps(row[-1])) for row in POS]
    write_csv(OUT / "purchase_orders.csv",
              ["po_number", "vendor_id", "po_date", "status", "currency", "approved_amount",
               "opening_invoiced_amount", "tolerance_percent", "tolerance_amount", "tax_treatment", "tax_rate", "line_items_json"], po_rows)
    write_csv(OUT / "bank_statement.csv", ["Date", "Description", "Debit", "Credit", "Reference"], [
        ("2026-01-22", "Partial payment Kaveri Paper Mart KPM-331", "15000.00", "", "UTR-AP-1001"),
        ("2026-01-27", "NEFT Metro Facility Services MFS-908", "11800.00", "", "UTR-AP-1002"),
        ("2026-02-06", "NEFT Blue Lotus Packaging BLP-9001", "12000.00", "", "UTR-AP-1003"),
        ("2026-02-13", "Partial payment Blue Lotus Packaging BLP-9002", "6000.00", "", "UTR-AP-1004"),
        ("2026-02-14", "Card purchase Hardware World Bengaluru", "9600.00", "", "CARD-AP-1005"),
    ])
    for spec in INVOICES:
        make_invoice(spec)
    live_dir = OUT / "live_upload"
    live_dir.mkdir(exist_ok=True)
    make_invoice(dict(filename="09_blue_lotus_live_upload.pdf", vendor="Blue Lotus Packaging",
                      number="BLP-9010", date="05 Mar 2026", po="PO-PACK-402",
                      subtotal="13559.32", tax="2440.68", total="16000.00",
                      desc="Protective sleeves", unit="13559.32", layout=1,
                      tax_note="GST included in total",
                      gstin="29BLUE12345A1Z5"), live_dir)
    print(f"Generated {len(INVOICES)} invoices, {len(VENDORS)} vendors and {len(POS)} purchase orders in {OUT}")


if __name__ == "__main__":
    main()
