from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Outcome = Literal[
    "matched",
    "partially_paid",
    "unpaid",
    "unmatched_bank",
    "needs_review",
]


class InvoiceRecord(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})

    id: str
    source_file_id: str | None = None
    original_filename: str | None = None
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    total_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    currency: str = "INR"
    page_number: int | None = None
    raw_text: str = ""
    flags: list[str] = Field(default_factory=list)


class BankTransactionRecord(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})

    id: str
    source_file_id: str | None = None
    original_filename: str | None = None
    transaction_date: date | None = None
    description: str
    debit_amount: Decimal = Decimal("0.00")
    credit_amount: Decimal = Decimal("0.00")
    reference: str | None = None
    row_number: int | None = None
    flags: list[str] = Field(default_factory=list)


class MatchCandidate(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})

    transaction_id: str
    score: int
    amount: Decimal
    amount_kind: Literal["exact", "partial", "near_mismatch", "overpayment", "mismatch"]
    date_delta_days: int | None = None
    reason_codes: list[str]


class ReconciliationSuggestion(BaseModel):
    model_config = ConfigDict(json_encoders={Decimal: str})

    id: str
    invoice_id: str | None = None
    bank_transaction_id: str | None = None
    outcome: Outcome
    matched_amount: Decimal = Decimal("0.00")
    outstanding_amount: Decimal = Decimal("0.00")
    candidate_transaction_ids: list[str] = Field(default_factory=list)
    selected_transaction_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    reason_text: str
    confidence: Decimal = Decimal("0.00")
    needs_review: bool = False

