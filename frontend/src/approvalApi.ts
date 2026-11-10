import { API_BASE } from "./api";

export type ApprovalStatus = "approved" | "needs_review" | "rejected";
export type StageStatus = "pending" | "running" | "completed" | "failed";

export interface ApprovalRun {
  id: string;
  invoice_id?: string | null;
  filename: string;
  status: "pending" | "running" | "completed" | "failed";
  error?: string | null;
  created_at: string;
  finished_at?: string | null;
  suggested_status?: ApprovalStatus | null;
  final_status?: ApprovalStatus | null;
  events?: StageEvent[];
}

export interface StageEvent {
  id: string;
  stage: string;
  status: StageStatus;
  explanation: string;
  output: Record<string, unknown>;
  created_at: string;
}

export interface ApprovalSummary {
  total_invoices: number;
  status_breakdown: Record<ApprovalStatus, number>;
  total_value: string;
  awaiting_review_value: string;
  recent_runs: ApprovalRun[];
}

export interface ApprovalRow {
  invoice_id: string;
  run_id: string;
  vendor_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  total_amount?: string | null;
  original_filename: string;
  source_file_id?: string | null;
  matched_po?: string | null;
  suggested_status: ApprovalStatus;
  final_status: ApprovalStatus;
  reason_codes: string[];
  created_at: string;
}

export interface InvoiceFields {
  vendor_name?: string | null;
  vendor_address?: string | null;
  gstin?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  po_number?: string | null;
  currency?: string | null;
  subtotal?: string | null;
  taxable_amount?: string | null;
  tax_amount?: string | null;
  tax_rate?: string | null;
  total_amount?: string | null;
  tax_inclusive?: boolean | null;
  line_items: Array<{ description: string; quantity?: string | null; unit_price?: string | null; line_total?: string | null }>;
  field_pages: Record<string, number>;
  warnings: string[];
}

export interface ApprovalDetail extends ApprovalRow {
  extraction_method: string;
  raw_text: string;
  extraction: InvoiceFields;
  suggestion: {
    suggested_status: ApprovalStatus;
    matched_po?: string | null;
    candidate_pos: string[];
    checks: Array<{ code: string; outcome: "pass" | "warning" | "fail"; explanation: string }>;
    reason_codes: string[];
    explanation: string;
    remaining_po_balance?: string | null;
    difference?: string | null;
    tolerance?: string | null;
    duplicate_of?: string | null;
  };
  selected_po?: string | null;
  payment?: { id: string; outcome: string; matched_amount: string; outstanding_amount: string; reason_text: string } | null;
  reviews: Array<{ id: string; action: string; final_status: ApprovalStatus; corrections: Record<string, unknown>; selected_po?: string | null; note: string; previous_suggestion: string; created_at: string }>;
  events: StageEvent[];
}

export interface PurchaseOrder {
  number: string;
  vendor_id: string;
  status: string;
  approved_amount: string;
  remaining_balance: string;
  tolerance_percent: string;
  tolerance_amount: string;
  tax_rate?: string | null;
  line_items: Array<{ description: string; quantity?: string | null; unit_price?: string | null; line_total?: string | null }>;
}

export interface SampleDocument {
  filename: string;
  folder: string;
  title: string;
  scenario: string;
  processed: boolean;
  preloaded?: boolean;
  upload_page: "Invoice approval" | "Payment reconciliation";
}

export interface DemoCatalog {
  invoices: SampleDocument[];
  bank_statement: SampleDocument;
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const approvalExportUrl = `${API_BASE}/api/approval/export`;
export const sourceUrl = (sourceId: string) => `${API_BASE}/api/source-files/${sourceId}`;
export const sourcePreviewUrl = (sourceId: string) => `${API_BASE}/api/source-files/${sourceId}/preview`;
export const fetchApprovalSummary = () => get<ApprovalSummary>("/api/approval/summary");
export const fetchApprovalRows = (search = "", status = "all") => get<{ invoices: ApprovalRow[] }>(`/api/approval/invoices?${new URLSearchParams({ search, status })}`);
export const fetchApprovalDetail = (id: string) => get<ApprovalDetail>(`/api/approval/invoices/${id}`);
export const fetchApprovalRuns = () => get<{ runs: ApprovalRun[] }>("/api/approval/runs");
export const fetchApprovalRun = (id: string) => get<ApprovalRun>(`/api/approval/runs/${id}`);
export const fetchPurchaseOrders = () => get<{ purchase_orders: PurchaseOrder[] }>("/api/approval/purchase-orders");
export const fetchDemoCatalog = () => get<DemoCatalog>("/api/demo/catalog");

export function startApproval(files: File[]) {
  const body = new FormData();
  files.forEach(file => body.append("invoices", file));
  return get<{ runs: Array<{ run_id: string; filename: string }> }>("/api/approval/process", { method: "POST", body });
}

export function uploadProcurement(vendors: File | null, pos: File | null) {
  const body = new FormData();
  if (vendors) body.append("vendor_csv", vendors);
  if (pos) body.append("po_csv", pos);
  return get<{ vendors: number; purchase_orders: number }>("/api/approval/procurement", { method: "POST", body });
}

export function submitApprovalReview(id: string, payload: {
  action: "approve" | "reject";
  corrections: Record<string, unknown>;
  selected_po?: string | null;
  note: string;
}) {
  return get<{ message: string; final_status: ApprovalStatus }>(`/api/approval/invoices/${id}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
}
