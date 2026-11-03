from pathlib import Path
import csv

import fitz
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test_uploads_2"


def make_text_pdf(path: Path, lines: list[str]) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 72
    for index, line in enumerate(lines):
        size = 16 if index == 0 else 11
        page.insert_text((72, y), line, fontsize=size, fontname="helv", fill=(0.05, 0.07, 0.10))
        y += 24 if index == 0 else 18
    doc.save(path)
    doc.close()


def load_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def make_image_pdf(path: Path, lines: list[str]) -> None:
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 80, 1150, 1650), outline=(28, 53, 73), width=4)
    draw.text((130, 130), lines[0], fill=(17, 70, 85), font=load_font(48))
    y = 225
    for line in lines[1:]:
        draw.text((130, y), line, fill=(20, 31, 43), font=load_font(31))
        y += 54
    image.save(path, "PDF", resolution=180.0)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for existing in OUT.iterdir():
        if existing.is_file():
            existing.unlink()

    make_text_pdf(
        OUT / "01_metro_facility_services.pdf",
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
        ],
    )
    make_text_pdf(
        OUT / "02_kaveri_paper_mart.pdf",
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
        ],
    )
    make_text_pdf(
        OUT / "03_pixelcraft_design_studio.pdf",
        [
            "PixelCraft Design Studio",
            "Invoice Number: PCD-2026-009",
            "Invoice Date: 14 Jan 2026",
            "Client: FinGuard Evaluation Lab",
            "Description: Dashboard prototype design sprint",
            "Subtotal: INR 27,542.37",
            "Tax: INR 4,957.63",
            "Amount Due: INR 32,500.00",
        ],
    )
    make_text_pdf(
        OUT / "04_blue_lotus_packaging_unstructured.pdf",
        [
            "Blue Lotus Packaging",
            "Hello FinGuard Evaluation Lab,",
            "This memo confirms packaging material delivered on 05 February 2026.",
            "Our billing reference for your records is BLP-9001.",
            "Please arrange settlement of INR 12,800.00 including all applicable taxes.",
            "Tax component included in the above amount is INR 1,952.54.",
            "Items supplied: corrugated cartons, labels, protective sleeves.",
        ],
    )
    make_image_pdf(
        OUT / "05_cedar_cleaning_care_scanned.pdf",
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
        ],
    )

    with (OUT / "bank_statement_january.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Description", "Debit", "Credit", "Reference"])
        writer.writerow(["2026-01-22", "Partial payment Kaveri Paper Mart KPM-331", "15000.00", "", "UTR-EVAL-1001"])
        writer.writerow(["2026-01-24", "Card purchase Hardware World Bengaluru", "9600.00", "", "CARD-EVAL-1002"])
        writer.writerow(["2026-01-27", "NEFT Metro Facility Services MFS-908", "11800.00", "", "UTR-EVAL-1003"])
        writer.writerow(["2026-01-28", "Duplicate NEFT Metro Facility Services MFS-908", "11800.00", "", "UTR-EVAL-1004"])

    with (OUT / "bank_statement_february.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Description", "Debit", "Credit", "Reference"])
        writer.writerow(["2026-02-10", "NEFT Blue Lotus Packaging BLP-9001", "12800.00", "", "UTR-EVAL-2001"])
        writer.writerow(["2026-02-12", "NEFT Cedar Cleaning Care CFC-778", "9500.00", "", "UTR-EVAL-2002"])

    for path in sorted(OUT.iterdir()):
        print(path)


if __name__ == "__main__":
    main()
