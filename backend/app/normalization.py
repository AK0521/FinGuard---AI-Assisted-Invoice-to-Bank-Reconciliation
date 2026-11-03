from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")
COMPANY_STOPWORDS = {
    "private",
    "limited",
    "pvt",
    "ltd",
    "llp",
    "india",
    "services",
    "service",
    "solutions",
    "and",
    "co",
    "company",
}


def money(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace(",", "")
    text = re.sub(r"(inr|rs\.?|₹)", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", ".", "-", "-."}:
        return None
    try:
        amount = Decimal(text).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def as_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def normalize_invoice_number(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_reference(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def vendor_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    raw = re.sub(r"[^a-z0-9 ]", " ", value.lower())
    tokens = {token for token in raw.split() if len(token) > 1}
    return tokens - COMPANY_STOPWORDS


def canonical_vendor(value: str | None) -> str:
    return " ".join(sorted(vendor_tokens(value)))


def vendor_score(vendor_name: str | None, description: str | None) -> float:
    vendor = vendor_tokens(vendor_name)
    desc = vendor_tokens(description)
    if not vendor or not desc:
        return 0.0
    return len(vendor & desc) / max(len(vendor), 1)


def contains_partial_language(description: str | None) -> bool:
    if not description:
        return False
    return bool(re.search(r"\b(part|partial|advance|installment|instalment|tranche)\b", description, re.I))


def date_delta_days(left: date | None, right: date | None) -> int | None:
    if left is None or right is None:
        return None
    return (right - left).days

