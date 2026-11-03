import type { AskResponse, DetailResponse, LlmStatus, ResultRow, Summary } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function loadDemo() {
  return request<{ message: string }>("/api/demo", { method: "POST" });
}

export function fetchSummary() {
  return request<Summary>("/api/summary");
}

export function fetchLlmStatus() {
  return request<LlmStatus>("/api/llm/status");
}

export function fetchReconciliations(search = "", status = "all") {
  const params = new URLSearchParams({ search, status });
  return request<{ results: ResultRow[] }>(`/api/reconciliations?${params.toString()}`);
}

export function fetchDetail(resultId: string) {
  return request<DetailResponse>(`/api/reconciliations/${resultId}`);
}

export function saveReview(
  resultId: string,
  payload: {
    action: "approve" | "reject" | "correct";
    final_status: string;
    selected_transaction_ids: string[];
    matched_amount?: string;
    outstanding_amount?: string;
    note?: string;
  }
) {
  return request<{ message: string }>(`/api/reconciliations/${resultId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export function askQuestion(question: string, resultId?: string | null) {
  return request<AskResponse>("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, result_id: resultId || null })
  });
}

export async function uploadWorkspace(formData: FormData) {
  return request<{ message: string; messages: string[]; invoices: number; bank_transactions: number }>(
    "/api/upload",
    {
      method: "POST",
      body: formData
    }
  );
}

export const exportUrl = `${API_BASE}/api/export`;

