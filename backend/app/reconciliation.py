from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .models import BankTransactionRecord, InvoiceRecord, MatchCandidate, ReconciliationSuggestion
from .normalization import (
    contains_partial_language,
    date_delta_days,
    normalize_invoice_number,
    normalize_reference,
    vendor_score,
)


DATE_WINDOW_DAYS = 45
EARLY_PAYMENT_GRACE_DAYS = 5


def _payment_amount(tx: BankTransactionRecord) -> Decimal:
    if tx.debit_amount and tx.debit_amount > 0:
        return tx.debit_amount
    return Decimal("0.00")


def _date_ok(delta: int | None, window_days: int) -> bool:
    if delta is None:
        return True
    return -EARLY_PAYMENT_GRACE_DAYS <= delta <= window_days


def _amount_kind(invoice: InvoiceRecord, tx: BankTransactionRecord) -> str:
    total = invoice.total_amount or Decimal("0.00")
    amount = _payment_amount(tx)
    if amount == total:
        return "exact"
    if amount > total:
        return "overpayment"
    diff = total - amount
    tolerance = max(Decimal("100.00"), (total * Decimal("0.02")).quantize(Decimal("0.01")))
    if diff <= tolerance:
        return "near_mismatch"
    if contains_partial_language(tx.description) or amount <= (total * Decimal("0.90")):
        return "partial"
    return "mismatch"


def _candidate_for(
    invoice: InvoiceRecord,
    tx: BankTransactionRecord,
    window_days: int,
) -> MatchCandidate | None:
    if invoice.total_amount is None or invoice.total_amount <= 0:
        return None
    amount = _payment_amount(tx)
    if amount <= 0:
        return None

    joined_text = f"{tx.description} {tx.reference or ''}"
    invoice_no = normalize_invoice_number(invoice.invoice_number)
    ref_text = normalize_reference(joined_text)
    ref_match = bool(invoice_no and invoice_no in ref_text)
    v_score = vendor_score(invoice.vendor_name, joined_text)
    vendor_match = v_score >= 0.5
    delta = date_delta_days(invoice.invoice_date, tx.transaction_date)
    date_ok = _date_ok(delta, window_days)

    if not date_ok or not (ref_match or vendor_match):
        return None

    amount_kind = _amount_kind(invoice, tx)
    codes: list[str] = []
    score = 0
    if ref_match:
        codes.append("invoice_reference_match")
        score += 55
    if vendor_match:
        codes.append("vendor_name_match")
        score += 25
    if delta is None:
        codes.append("date_missing_or_uncertain")
        score += 5
    else:
        codes.append("date_within_window")
        score += 10
    if amount_kind == "exact":
        codes.append("amount_exact")
        score += 30
    elif amount_kind == "partial":
        codes.append("partial_amount")
        score += 15
    elif amount_kind == "near_mismatch":
        codes.append("amount_near_mismatch")
        score += 8
    elif amount_kind == "overpayment":
        codes.append("amount_overpayment")
    else:
        codes.append("amount_mismatch")

    return MatchCandidate(
        transaction_id=tx.id,
        score=score,
        amount=amount,
        amount_kind=amount_kind,  # type: ignore[arg-type]
        date_delta_days=delta,
        reason_codes=codes,
    )


def _reason_text(codes: list[str]) -> str:
    code_set = set(codes)
    if "bank_transaction_without_invoice" in code_set:
        return (
            "This bank payment is marked as unmatched because none of the uploaded invoices matched its "
            "reference, vendor text, amount, and payment date window."
        )
    if "no_candidate_found" in code_set:
        return (
            "This invoice is marked unpaid because no bank payment matched the invoice reference, vendor name, "
            "amount, and allowed payment date window."
        )
    if "missing_required_fields" in code_set:
        return (
            "This record needs review because one or more required invoice fields could not be extracted with "
            "enough confidence."
        )
    if "duplicate_invoice_candidate" in code_set:
        return (
            "This record needs review because another invoice has the same invoice number and amount, so the "
            "bank payment cannot be assigned safely without analyst confirmation."
        )
    if "ambiguous_invoice_assignment" in code_set:
        return (
            "This bank transaction could belong to more than one invoice. It has not been assigned or counted "
            "as paid until an analyst selects the correct invoice."
        )
    if "ambiguous_candidates" in code_set:
        return (
            "This record needs review because more than one plausible bank transaction was found. An analyst "
            "should choose the correct payment before approving the final status."
        )
    if "missing_invoice_number" in code_set:
        return (
            "This invoice needs review because the invoice number is missing, even though the vendor, date, "
            "or amount may resemble a bank payment."
        )
    if "amount_mismatch" in code_set:
        return (
            "A possible payment was found from the reference or vendor text, but the paid amount does not "
            "match the invoice total, so the result needs analyst review."
        )
    if "amount_overpayment" in code_set:
        return (
            "A possible payment was found, but the bank amount is higher than the invoice total. The match "
            "needs review before it is treated as settled."
        )
    if "amount_near_mismatch" in code_set:
        return (
            "A likely payment was found, but the amount is slightly different from the invoice total. This "
            "should be checked before approving the result."
        )
    if "split_payment" in code_set:
        return (
            "Multiple bank payments together cover the invoice total, so the invoice is treated as paid by "
            "split payment evidence."
        )
    if "partial_amount" in code_set:
        return (
            "A matching bank payment was found, but it covers only part of the invoice total. The remaining "
            "balance stays outstanding."
        )
    if {"invoice_reference_match", "vendor_name_match", "date_within_window", "amount_exact"}.issubset(code_set):
        return (
            "This invoice is marked paid because the bank payment matched the invoice reference, vendor name, "
            "payment date window, and invoice total."
        )
    labels = {
        "invoice_reference_match": "the invoice reference appears in the bank payment",
        "vendor_name_match": "the vendor name matches the bank description",
        "date_within_window": "the payment date falls inside the allowed window",
        "amount_exact": "the bank amount equals the invoice total",
    }
    parts = [labels.get(code, code.replace("_", " ")) for code in codes]
    return "The reconciliation engine found this evidence: " + "; ".join(parts) + "."


def reconcile_records(
    invoices: list[InvoiceRecord],
    bank_transactions: list[BankTransactionRecord],
    window_days: int = DATE_WINDOW_DAYS,
) -> list[ReconciliationSuggestion]:
    duplicate_keys = Counter(
        (
            normalize_reference(inv.vendor_name),
            normalize_invoice_number(inv.invoice_number),
            str(inv.total_amount or ""),
        )
        for inv in invoices
        if normalize_invoice_number(inv.invoice_number) and inv.total_amount is not None
    )

    results: list[ReconciliationSuggestion] = []
    all_candidate_tx_ids: set[str] = set()
    selected_tx_ids: set[str] = set()
    known_numbers = {normalize_invoice_number(inv.invoice_number) for inv in invoices if inv.invoice_number}
    candidate_map: dict[str, list[MatchCandidate]] = {}
    tx_claims: dict[str, set[str]] = {}
    for invoice in invoices:
        invoice_no = normalize_invoice_number(invoice.invoice_number)
        eligible: list[MatchCandidate] = []
        for tx in bank_transactions:
            bank_text = normalize_reference(f"{tx.description} {tx.reference or ''}")
            mentioned = {number for number in known_numbers if number in bank_text}
            # An explicit reference to another invoice outranks a vendor-only match.
            if mentioned and invoice_no not in mentioned:
                continue
            candidate = _candidate_for(invoice, tx, window_days)
            if candidate is not None:
                eligible.append(candidate)
                tx_claims.setdefault(tx.id, set()).add(invoice.id)
        candidate_map[invoice.id] = sorted(eligible, key=lambda item: item.score, reverse=True)
    shared_transactions = {tx_id for tx_id, claims in tx_claims.items() if len(claims) > 1}

    for invoice in invoices:
        missing = []
        if not invoice.vendor_name:
            missing.append("vendor_name")
        if not invoice.invoice_date:
            missing.append("invoice_date")
        if invoice.total_amount is None:
            missing.append("total_amount")

        duplicate_key = (normalize_reference(invoice.vendor_name), normalize_invoice_number(invoice.invoice_number), str(invoice.total_amount or ""))
        has_duplicate_invoice = bool(duplicate_key[0] and duplicate_keys[duplicate_key] > 1)

        candidates = candidate_map[invoice.id]
        candidate_ids = [candidate.transaction_id for candidate in candidates]
        all_candidate_tx_ids.update(candidate_ids)

        if missing:
            codes = ["missing_required_fields", *invoice.flags]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="needs_review",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids,
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.20"),
                    needs_review=True,
                )
            )
            continue

        if not candidates:
            codes = ["no_candidate_found", *invoice.flags]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="unpaid",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.75"),
                    needs_review=False,
                )
            )
            continue

        if has_duplicate_invoice:
            codes = ["duplicate_invoice_candidate", *candidates[0].reason_codes]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="needs_review",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids,
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.45"),
                    needs_review=True,
                )
            )
            continue

        if not invoice.invoice_number:
            codes = ["missing_invoice_number", *candidates[0].reason_codes]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="needs_review",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids,
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.50"),
                    needs_review=True,
                )
            )
            continue

        if any(candidate.transaction_id in shared_transactions for candidate in candidates):
            codes = ["ambiguous_invoice_assignment", *candidates[0].reason_codes]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}", invoice_id=invoice.id, outcome="needs_review",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids, reason_codes=codes,
                    reason_text=_reason_text(codes), confidence=Decimal("0.40"), needs_review=True,
                )
            )
            continue

        exact_candidates = [candidate for candidate in candidates if candidate.amount_kind == "exact"]
        partial_candidates = [candidate for candidate in candidates if candidate.amount_kind == "partial"]
        problem_candidates = [
            candidate
            for candidate in candidates
            if candidate.amount_kind in {"near_mismatch", "overpayment", "mismatch"}
        ]

        if len(exact_candidates) == 1 and not problem_candidates:
            selected = exact_candidates[0]
            selected_tx_ids.add(selected.transaction_id)
            codes = selected.reason_codes
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="matched",
                    matched_amount=invoice.total_amount or Decimal("0.00"),
                    outstanding_amount=Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids,
                    selected_transaction_ids=[selected.transaction_id],
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.96"),
                    needs_review=False,
                )
            )
            continue

        if len(exact_candidates) > 1:
            codes = ["ambiguous_candidates", *exact_candidates[0].reason_codes]
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome="needs_review",
                    outstanding_amount=invoice.total_amount or Decimal("0.00"),
                    candidate_transaction_ids=candidate_ids,
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=Decimal("0.48"),
                    needs_review=True,
                )
            )
            continue

        if partial_candidates and not exact_candidates and not problem_candidates:
            total_paid = sum((candidate.amount for candidate in partial_candidates), Decimal("0.00"))
            selected_ids = [candidate.transaction_id for candidate in partial_candidates]
            for tx_id in selected_ids:
                selected_tx_ids.add(tx_id)
            if total_paid == invoice.total_amount:
                codes = ["split_payment", *partial_candidates[0].reason_codes]
                outcome = "matched"
                outstanding = Decimal("0.00")
                confidence = Decimal("0.88")
            elif total_paid < (invoice.total_amount or Decimal("0.00")):
                codes = partial_candidates[0].reason_codes
                outcome = "partially_paid"
                outstanding = (invoice.total_amount or Decimal("0.00")) - total_paid
                confidence = Decimal("0.82")
            else:
                codes = ["amount_overpayment", *partial_candidates[0].reason_codes]
                outcome = "needs_review"
                outstanding = Decimal("0.00")
                confidence = Decimal("0.40")
            results.append(
                ReconciliationSuggestion(
                    id=f"rec_{invoice.id}",
                    invoice_id=invoice.id,
                    outcome=outcome,  # type: ignore[arg-type]
                    matched_amount=total_paid,
                    outstanding_amount=outstanding,
                    candidate_transaction_ids=candidate_ids,
                    selected_transaction_ids=selected_ids if outcome != "needs_review" else [],
                    reason_codes=codes,
                    reason_text=_reason_text(codes),
                    confidence=confidence,
                    needs_review=outcome == "needs_review",
                )
            )
            continue

        codes = ["ambiguous_candidates" if len(candidates) > 1 else "amount_mismatch"]
        codes.extend(candidates[0].reason_codes)
        results.append(
            ReconciliationSuggestion(
                id=f"rec_{invoice.id}",
                invoice_id=invoice.id,
                outcome="needs_review",
                outstanding_amount=invoice.total_amount or Decimal("0.00"),
                candidate_transaction_ids=candidate_ids,
                reason_codes=codes,
                reason_text=_reason_text(codes),
                confidence=Decimal("0.42"),
                needs_review=True,
            )
        )

    for tx in bank_transactions:
        if _payment_amount(tx) <= 0:
            continue
        if tx.id in selected_tx_ids or tx.id in all_candidate_tx_ids:
            continue
        codes = ["bank_transaction_without_invoice"]
        results.append(
            ReconciliationSuggestion(
                id=f"rec_{tx.id}",
                bank_transaction_id=tx.id,
                outcome="unmatched_bank",
                matched_amount=_payment_amount(tx),
                outstanding_amount=Decimal("0.00"),
                reason_codes=codes,
                reason_text=_reason_text(codes),
                confidence=Decimal("0.85"),
                needs_review=True,
            )
        )

    return results

