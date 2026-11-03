export type Status =
  | "matched"
  | "partially_paid"
  | "unpaid"
  | "unmatched_bank"
  | "needs_review";

export interface Summary {
  dataset_label: string;
  total_invoices: number;
  reconciled_amount: string;
  outstanding_amount: string;
  items_needing_review: number;
  status_breakdown: Record<string, number>;
  total_results: number;
}

export interface ResultRow {
  id: string;
  invoice_id?: string | null;
  bank_transaction_id?: string | null;
  outcome: Status;
  final_status: Status;
  vendor_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  invoice_total?: string | null;
  invoice_filename?: string | null;
  bank_description?: string | null;
  bank_transaction_date?: string | null;
  bank_debit_amount?: string | null;
  bank_reference?: string | null;
  matched_amount: string;
  outstanding_amount: string;
  suggested_matched_amount?: string;
  suggested_outstanding_amount?: string;
  candidate_transaction_ids: string[];
  selected_transaction_ids: string[];
  reason_text: string;
  reason_codes: string[];
  confidence: string;
  needs_review: boolean;
  review_action?: string | null;
  review_note?: string | null;
}

export interface InvoiceDetail {
  id: string;
  original_filename?: string | null;
  vendor_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  total_amount?: string | null;
  tax_amount?: string | null;
  currency: string;
  page_number?: number | null;
  flags: string[];
}

export interface BankTransaction {
  id: string;
  original_filename?: string | null;
  transaction_date?: string | null;
  description: string;
  debit_amount: string;
  credit_amount: string;
  reference?: string | null;
  row_number?: number | null;
  flags: string[];
}

export interface ReviewDecision {
  id: string;
  action: string;
  final_status: Status;
  selected_transaction_ids: string[];
  final_matched_amount?: string | null;
  final_outstanding_amount?: string | null;
  note?: string | null;
  created_at: string;
}

export interface DetailResponse {
  result: ResultRow;
  invoice?: InvoiceDetail | null;
  bank_transaction?: BankTransaction | null;
  candidates: BankTransaction[];
  selected_transactions: BankTransaction[];
  reviews: ReviewDecision[];
}

export interface Citation {
  source_type: string;
  source_record_id: string;
  label: string;
  locator: string;
  excerpt: string;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  answer_mode?: string;
}

export interface LlmStatus {
  enabled: boolean;
  available: boolean;
  model: string;
  message: string;
}

