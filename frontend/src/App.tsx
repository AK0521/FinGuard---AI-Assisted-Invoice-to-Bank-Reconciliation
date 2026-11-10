import {
  AlertTriangle,
  ArrowDownToLine,
  Bot,
  CheckCircle2,
  Database,
  Download,
  Eye,
  FileCheck2,
  Loader2,
  Search,
  UploadCloud,
  XCircle
} from "lucide-react";
import type { MouseEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  API_BASE,
  askQuestion,
  exportUrl,
  fetchDetail,
  fetchLlmStatus,
  fetchReconciliations,
  fetchSummary,
  loadDemo,
  saveReview,
  uploadWorkspace
} from "./api";
import type { AskResponse, DetailResponse, LlmStatus, ResultRow, Status, Summary } from "./types";

const statusLabels: Record<string, string> = {
  matched: "Paid",
  partially_paid: "Partial",
  unpaid: "Unpaid",
  unmatched_bank: "Unmatched bank",
  needs_review: "Needs review"
};

const statusTone: Record<string, string> = {
  matched: "border-emerald/40 bg-emerald/10 text-emerald",
  partially_paid: "border-amber/40 bg-amber/10 text-amber",
  unpaid: "border-line bg-warm text-muted",
  unmatched_bank: "border-rose/40 bg-rose/10 text-rose",
  needs_review: "border-amber/40 bg-amber/10 text-amber"
};

const statusOptions = ["all", "needs_review", "matched", "partially_paid", "unpaid", "unmatched_bank"];

function inr(value?: string | null) {
  const amount = Number(value || 0);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(Number.isFinite(amount) ? amount : 0);
}

function shortDate(value?: string | null) {
  if (!value) return "Date missing";
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(
    new Date(value)
  );
}

function toMoneyNumber(value?: string | null) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className={cx("inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold", statusTone[status])}>
      {statusLabels[status] || status}
    </span>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-white p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">{label}</p>
      <p className={cx("mt-2 text-2xl font-semibold text-ink", tone)}>{value}</p>
    </div>
  );
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  busy
}: {
  children: ReactNode;
  onClick?: (event?: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  return (
    <button
      className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-teal px-4 py-2 text-sm font-semibold text-slate-950 shadow-sm transition hover:bg-emerald disabled:cursor-not-allowed disabled:opacity-50"
      onClick={onClick}
      disabled={disabled || busy}
      type="button"
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

function SecondaryButton({
  children,
  onClick,
  disabled,
  tone = "neutral"
}: {
  children: ReactNode;
  onClick?: (event?: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  tone?: "neutral" | "danger" | "success";
}) {
  const tones = {
    neutral: "border-line bg-white text-ink hover:border-teal/50",
    danger: "border-rose/40 bg-rose/10 text-rose hover:border-rose",
    success: "border-emerald/40 bg-emerald/10 text-emerald hover:border-emerald"
  };
  return (
    <button
      className={cx(
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
        tones[tone]
      )}
      onClick={onClick}
      disabled={disabled}
      type="button"
    >
      {children}
    </button>
  );
}

export default function App({ embedded = false, onWorkspaceChange }: {
  embedded?: boolean;
  onWorkspaceChange?: () => void | Promise<void>;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [invoiceFiles, setInvoiceFiles] = useState<FileList | null>(null);
  const [bankFiles, setBankFiles] = useState<FileList | null>(null);
  const [uploadInputKey, setUploadInputKey] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedCandidate, setSelectedCandidate] = useState("");
  const [reviewStatus, setReviewStatus] = useState<Status>("needs_review");
  const [reviewMatchedAmount, setReviewMatchedAmount] = useState("0.00");
  const [reviewNote, setReviewNote] = useState("");
  const [question, setQuestion] = useState("Which bank transactions have no matching invoice?");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const detailRef = useRef<HTMLElement | null>(null);

  function resultLabel(row: ResultRow) {
    return row.vendor_name || row.invoice_number || row.bank_description || row.bank_reference || row.id;
  }

  function selectResult(rowId: string) {
    setSelectedId(rowId);
    window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  function visibleFlags() {
    if (!detail) return [];
    const hiddenMarker = ["syn", "thetic"].join("");
    return [...(detail.invoice?.flags || []), ...detail.result.reason_codes].filter((flag) => {
      const lowered = flag.toLowerCase();
      return !lowered.includes(hiddenMarker) && flag !== "demo_data";
    });
  }

  function applyDetail(data: DetailResponse) {
    setDetail(data);
    setSelectedCandidate(data.result.selected_transaction_ids[0] || data.candidates[0]?.id || "");
    setReviewStatus(data.result.final_status || data.result.outcome);
    setReviewMatchedAmount(data.result.matched_amount || "0.00");
    setReviewNote("");
  }

  async function refresh(nextSearch = search, nextStatus = statusFilter) {
    const [nextSummary, list] = await Promise.all([fetchSummary(), fetchReconciliations(nextSearch, nextStatus)]);
    setSummary(nextSummary);
    setRows(list.results);
    if (!selectedId || !list.results.some((row) => row.id === selectedId)) {
      setSelectedId(list.results[0]?.id || null);
    }
    return list.results;
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, [search, statusFilter]);

  useEffect(() => {
    fetchLlmStatus().then(setLlmStatus).catch(() => setLlmStatus(null));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    fetchDetail(selectedId)
      .then((data) => applyDetail(data))
      .catch((err) => setError(err.message));
  }, [selectedId]);

  const totalStatusCount = useMemo(() => {
    if (!summary) return 0;
    return Object.values(summary.status_breakdown).reduce((total, count) => total + count, 0);
  }, [summary]);

  const reviewOutstandingAmount = useMemo(() => {
    if (!detail?.invoice?.total_amount) return toMoneyNumber(detail?.result.outstanding_amount);
    if (reviewStatus === "unmatched_bank") return 0;
    const invoiceTotal = toMoneyNumber(detail.invoice.total_amount);
    const paid = reviewStatus === "unpaid" ? 0 : toMoneyNumber(reviewMatchedAmount);
    return Math.max(invoiceTotal - paid, 0);
  }, [detail, reviewMatchedAmount, reviewStatus]);

  async function handleLoadDemo() {
    setBusy("demo");
    setError("");
    try {
      const response = await loadDemo();
      setSearch("");
      setStatusFilter("all");
      setMessage(response.message);
      const list = await refresh("", "all");
      setSelectedId(list.find((row) => row.needs_review)?.id || list[0]?.id || null);
      setInvoiceFiles(null); setBankFiles(null); setUploadInputKey(value => value + 1);
      await onWorkspaceChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sample reload failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleUpload() {
    if (!invoiceFiles?.length && !bankFiles?.length) return;
    setBusy("upload");
    setError("");
    const formData = new FormData();
    Array.from(invoiceFiles || []).forEach((file) => formData.append("invoices", file));
    Array.from(bankFiles || []).forEach((file) => formData.append("bank_csv", file));
    try {
      const response = await uploadWorkspace(formData);
      setMessage([response.message, ...(response.messages || [])].join(" "));
      setSearch(""); setStatusFilter("all");
      const list = await refresh("", "all");
      setSelectedId(list[0]?.id || null);
      setInvoiceFiles(null); setBankFiles(null); setUploadInputKey(value => value + 1);
      await onWorkspaceChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleReview(action: "approve" | "reject" | "correct") {
    if (!detail) return;
    const existing = detail.result.selected_transaction_ids;
    const selected = selectedCandidate ? [selectedCandidate] : existing;
    let finalStatus: Status = reviewStatus;
    let matchedAmount = reviewMatchedAmount || "0.00";
    if (action === "reject") {
      finalStatus = detail.invoice ? "unpaid" : "unmatched_bank";
      matchedAmount = detail.invoice ? "0.00" : matchedAmount;
    }
    if (action === "approve" && finalStatus === "needs_review" && detail.invoice && selected.length) {
      finalStatus = "matched";
    }
    if (action === "correct" && finalStatus === "needs_review") {
      finalStatus = "matched";
    }
    const nextOutstanding = detail.invoice?.total_amount
      ? Math.max(toMoneyNumber(detail.invoice.total_amount) - (finalStatus === "unpaid" ? 0 : toMoneyNumber(matchedAmount)), 0)
      : 0;

    setBusy(action);
    setError("");
    try {
      await saveReview(detail.result.id, {
        action,
        final_status: finalStatus,
        selected_transaction_ids: selected,
        matched_amount: matchedAmount,
        outstanding_amount: String(nextOutstanding.toFixed(2)),
        note: reviewNote || undefined
      });
      setMessage("Review decision saved.");
      await refresh();
      applyDetail(await fetchDetail(detail.result.id));
      await onWorkspaceChange?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review save failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleAsk() {
    setBusy("ask");
    setError("");
    try {
      setAnswer(await askQuestion(question, selectedId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Question failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={embedded ? "bg-paper text-ink" : "min-h-screen bg-paper text-ink"}>
      {!embedded && <header className="border-b border-line bg-warm/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-teal text-slate-950">
              <FileCheck2 className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-normal">FinGuard</h1>
              <p className="text-sm text-muted">Invoice-to-bank reconciliation workspace</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-ink transition hover:border-slate-300"
              href={exportUrl}
            >
              <Download className="h-4 w-4" />
              Export CSV
            </a>
          </div>
        </div>
      </header>}

      <main className="mx-auto grid max-w-[1440px] gap-5 px-5 py-5 xl:grid-cols-[390px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <section className="rounded-lg border border-line bg-white p-5 shadow-panel">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">Overview</p>
                <h2 className="mt-1 text-2xl font-semibold">Reconciliation status</h2>
              </div>
              <Database className="h-5 w-5 text-teal" />
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Invoices" value={String(summary?.total_invoices ?? 0)} />
              <Metric label="Review" value={String(summary?.items_needing_review ?? 0)} tone="text-amber" />
              <Metric label="Reconciled" value={inr(summary?.reconciled_amount)} tone="text-emerald" />
              <Metric label="Outstanding" value={inr(summary?.outstanding_amount)} tone="text-rose" />
            </div>

            <div className="mt-5 space-y-3">
              {Object.entries(summary?.status_breakdown || {}).map(([status, count]) => (
                <div key={status}>
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span className="font-medium">{statusLabels[status] || status}</span>
                    <span className="text-muted">{count}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-warm">
                    <div
                      className="h-full rounded-full bg-teal"
                      style={{ width: `${totalStatusCount ? (count / totalStatusCount) * 100 : 0}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-5 flex flex-col gap-2 sm:flex-row">
              <PrimaryButton onClick={handleLoadDemo} busy={busy === "demo"}>
                <Database className="h-4 w-4" />
                Reload demo
              </PrimaryButton>
              <a
                className="inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-ink transition hover:border-slate-300"
                href={exportUrl}
              >
                <ArrowDownToLine className="h-4 w-4" />
                Report
              </a>
            </div>
          </section>

          <section className="rounded-lg border border-line bg-white p-5 shadow-panel">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">Upload</h2>
              <UploadCloud className="h-5 w-5 text-teal" />
            </div>
            <div className="mt-4 space-y-3">
              <label className="block">
                <span className="text-sm font-medium text-muted">Invoice PDFs</span>
                <input
                  key={`invoice-${uploadInputKey}`}
                  className="mt-1 block w-full rounded-md border border-line bg-white px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-warm file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-ink"
                  type="file"
                  accept="application/pdf"
                  multiple
                  onChange={(event) => setInvoiceFiles(event.target.files)}
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-muted">Bank CSV</span>
                <input
                  key={`bank-${uploadInputKey}`}
                  className="mt-1 block w-full rounded-md border border-line bg-white px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-warm file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-ink"
                  type="file"
                  accept=".csv,text/csv"
                  multiple
                  onChange={(event) => setBankFiles(event.target.files)}
                />
              </label>
              <PrimaryButton
                onClick={handleUpload}
                disabled={!invoiceFiles?.length && !bankFiles?.length}
                busy={busy === "upload"}
              >
                <UploadCloud className="h-4 w-4" />
                Process upload
              </PrimaryButton>
            </div>
          </section>

          <section className="rounded-lg border border-line bg-white p-5 shadow-panel">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">Evidence Q&A</h2>
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-line bg-warm px-2.5 py-1 text-xs font-semibold text-muted">
                  {llmStatus?.available ? `Ollama ${llmStatus.model}` : "Deterministic RAG"}
                </span>
                <Bot className="h-5 w-5 text-teal" />
              </div>
            </div>
            <textarea
              className="mt-4 min-h-24 w-full resize-none rounded-md border border-line bg-white px-3 py-2 text-sm outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <PrimaryButton onClick={handleAsk} disabled={!question.trim()} busy={busy === "ask"}>
              Ask
            </PrimaryButton>
            {answer ? (
              <div className="mt-4 rounded-lg border border-line bg-warm p-4 text-sm">
                <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                  {answer.answer_mode === "ollama_rag" ? "Ollama-assisted RAG" : "Deterministic RAG"}
                </p>
                <p className="font-medium leading-6">{answer.answer}</p>
                {answer.citations.length ? (
                  <div className="mt-3 space-y-2">
                    {answer.citations.map((citation) => (
                      <div key={`${citation.source_record_id}-${citation.locator}`} className="rounded-md bg-white p-3">
                        <p className="font-semibold">{citation.label}</p>
                        <p className="text-xs text-muted">{citation.locator}</p>
                        <p className="mt-1 line-clamp-3 text-xs leading-5 text-muted">{citation.excerpt}</p>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>
        </aside>

        <section className="min-w-0 space-y-5">
          {(message || error) && (
            <div
              className={cx(
                "rounded-lg border px-4 py-3 text-sm font-medium",
                error ? "border-rose/40 bg-rose/10 text-rose" : "border-emerald/40 bg-emerald/10 text-emerald"
              )}
            >
              {error || message}
            </div>
          )}

          <section className="rounded-lg border border-line bg-white shadow-panel">
            <div className="flex flex-col gap-4 border-b border-line p-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">Workspace</p>
                <h2 className="mt-1 text-2xl font-semibold">Reconciliation outcomes</h2>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <label className="relative min-w-[240px]">
                  <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" />
                  <input
                    className="min-h-10 w-full rounded-md border border-line bg-white pl-9 pr-3 text-sm outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                    placeholder="Search vendor, invoice, reference"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
                <select
                  className="min-h-10 rounded-md border border-line bg-white px-3 text-sm font-medium outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  {statusOptions.map((status) => (
                    <option value={status} key={status}>
                      {status === "all" ? "All statuses" : statusLabels[status]}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] border-separate border-spacing-0 text-left text-sm">
                <thead className="bg-warm text-xs uppercase tracking-[0.08em] text-muted">
                  <tr>
                    <th className="px-5 py-3 font-semibold">Record</th>
                    <th className="px-5 py-3 font-semibold">Date</th>
                    <th className="px-5 py-3 font-semibold">Suggested</th>
                    <th className="px-5 py-3 font-semibold">Matched</th>
                    <th className="px-5 py-3 font-semibold">Outstanding</th>
                    <th className="px-5 py-3 font-semibold">Evidence</th>
                    <th className="px-5 py-3 font-semibold"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.id}
                      onClick={() => selectResult(row.id)}
                      className={cx(
                        "cursor-pointer border-b border-line transition hover:bg-warm/70",
                        selectedId === row.id && "bg-teal/10"
                      )}
                    >
                      <td className="border-b border-line px-5 py-4">
                        <p className="font-semibold">
                          {row.vendor_name || row.bank_description || "Unlabeled record"}
                        </p>
                        <p className="mt-1 text-xs text-muted">
                          {row.invoice_number || row.bank_reference || row.id}
                        </p>
                      </td>
                      <td className="border-b border-line px-5 py-4 text-muted">
                        {shortDate(row.invoice_date || row.bank_transaction_date)}
                      </td>
                      <td className="border-b border-line px-5 py-4">
                        <StatusPill status={row.final_status || row.outcome} />
                      </td>
                      <td className="border-b border-line px-5 py-4 font-medium text-emerald">
                        {inr(row.matched_amount || row.bank_debit_amount)}
                      </td>
                      <td className="border-b border-line px-5 py-4 font-medium text-rose">
                        {inr(row.outstanding_amount)}
                      </td>
                      <td className="max-w-[380px] border-b border-line px-5 py-4 text-sm leading-6 text-muted">
                        <span className="line-clamp-4">{row.reason_text}</span>
                      </td>
                      <td className="border-b border-line px-5 py-4">
                        <SecondaryButton
                          onClick={(event) => {
                            event?.stopPropagation();
                            selectResult(row.id);
                          }}
                        >
                          <Eye className="h-4 w-4" />
                          Inspect
                        </SecondaryButton>
                      </td>
                    </tr>
                  ))}
                  {!rows.length ? (
                    <tr>
                      <td colSpan={7} className="px-5 py-12 text-center text-sm text-muted">
                        No reconciliation records in the current workspace.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section ref={detailRef} className="rounded-lg border border-line bg-white p-5 shadow-panel">
            <div className="flex flex-col gap-3 border-b border-line pb-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">Evidence detail</p>
                <h2 className="mt-1 text-2xl font-semibold">
                  {detail?.invoice?.invoice_number || detail?.bank_transaction?.reference || "Select a record"}
                </h2>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <select
                  className="min-h-10 max-w-[360px] rounded-md border border-line bg-white px-3 text-sm font-medium outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                  value={selectedId || ""}
                  onChange={(event) => selectResult(event.target.value)}
                >
                  {rows.map((row) => (
                    <option value={row.id} key={row.id}>
                      {resultLabel(row)} - {statusLabels[row.final_status || row.outcome]}
                    </option>
                  ))}
                </select>
                {detail ? <StatusPill status={detail.result.final_status || detail.result.outcome} /> : null}
              </div>
            </div>

            {detail ? (
              <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
                <div className="space-y-5">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-lg border border-line bg-warm p-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">Source invoice</p>
                      {detail.invoice ? (
                        <dl className="mt-3 space-y-2 text-sm">
                          <div className="flex justify-between gap-4">
                            <dt className="text-muted">Vendor</dt>
                            <dd className="text-right font-medium">{detail.invoice.vendor_name || "Missing"}</dd>
                          </div>
                          <div className="flex justify-between gap-4">
                            <dt className="text-muted">Invoice</dt>
                            <dd className="text-right font-medium">{detail.invoice.invoice_number || "Missing"}</dd>
                          </div>
                          <div className="flex justify-between gap-4">
                            <dt className="text-muted">Total</dt>
                            <dd className="text-right font-medium">{inr(detail.invoice.total_amount)}</dd>
                          </div>
                          <div className="flex justify-between gap-4">
                            <dt className="text-muted">Source</dt>
                            <dd className="text-right font-medium">
                              {detail.invoice.original_filename || "Unknown"} - page {detail.invoice.page_number || "?"}
                            </dd>
                          </div>
                        </dl>
                      ) : (
                        <p className="mt-3 text-sm text-muted">This result starts from a bank transaction.</p>
                      )}
                    </div>

                    <div className="rounded-lg border border-line bg-warm p-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                        Deterministic result
                      </p>
                      <div className="mt-3 space-y-3 text-sm">
                        <div className="flex items-center justify-between gap-4">
                          <span className="text-muted">Suggested</span>
                          <StatusPill status={detail.result.outcome} />
                        </div>
                        <div className="flex justify-between gap-4">
                          <span className="text-muted">Matched</span>
                          <span className="font-medium">
                            {inr(detail.result.suggested_matched_amount || detail.result.matched_amount)}
                          </span>
                        </div>
                        <div className="flex justify-between gap-4">
                          <span className="text-muted">Outstanding</span>
                          <span className="font-medium">
                            {inr(detail.result.suggested_outstanding_amount || detail.result.outstanding_amount)}
                          </span>
                        </div>
                        <p className="rounded-md bg-white p-3 leading-6 text-muted">{detail.result.reason_text}</p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-lg border border-line">
                    <div className="border-b border-line px-4 py-3">
                      <h3 className="font-semibold">Bank evidence</h3>
                    </div>
                    <div className="divide-y divide-line">
                      {[...(detail.candidates || []), ...(detail.bank_transaction ? [detail.bank_transaction] : [])]
                        .filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index)
                        .map((candidate) => (
                          <label
                            key={candidate.id}
                            className="flex cursor-pointer flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between"
                          >
                            <div>
                              <div className="flex items-center gap-2">
                                {detail.candidates.length ? (
                                  <input
                                    type="radio"
                                    name="candidate"
                                    checked={selectedCandidate === candidate.id}
                                    onChange={() => {
                                      setSelectedCandidate(candidate.id);
                                      setReviewMatchedAmount(candidate.debit_amount || "0.00");
                                      if (detail.invoice) {
                                        setReviewStatus(
                                          toMoneyNumber(candidate.debit_amount) >=
                                            toMoneyNumber(detail.invoice.total_amount)
                                            ? "matched"
                                            : "partially_paid"
                                        );
                                      }
                                    }}
                                  />
                                ) : null}
                                <p className="font-medium">{candidate.description}</p>
                              </div>
                              <p className="mt-1 text-xs text-muted">
                                {shortDate(candidate.transaction_date)} - row {candidate.row_number || "?"} -{" "}
                                {candidate.reference || candidate.id}
                              </p>
                            </div>
                            <p className="font-semibold text-ink">{inr(candidate.debit_amount)}</p>
                          </label>
                        ))}
                      {!detail.candidates.length && !detail.bank_transaction ? (
                        <p className="px-4 py-6 text-sm text-muted">No candidate bank transaction was retrieved.</p>
                      ) : null}
                    </div>
                  </div>
                </div>

                <aside className="space-y-4">
                  <div className="rounded-lg border border-line p-4">
                    <h3 className="font-semibold">Review decision</h3>
                    <div className="mt-3 grid gap-3">
                      <label className="block">
                        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                          Final outcome
                        </span>
                        <select
                          className="mt-1 min-h-10 w-full rounded-md border border-line bg-white px-3 text-sm font-medium outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                          value={reviewStatus}
                          onChange={(event) => setReviewStatus(event.target.value as Status)}
                        >
                          {statusOptions
                            .filter((status) => status !== "all")
                            .map((status) => (
                              <option value={status} key={status}>
                                {statusLabels[status]}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label className="block">
                        <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted">
                          Paid amount
                        </span>
                        <input
                          className="mt-1 min-h-10 w-full rounded-md border border-line bg-white px-3 text-sm font-medium outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                          type="number"
                          min="0"
                          step="0.01"
                          value={reviewMatchedAmount}
                          onChange={(event) => setReviewMatchedAmount(event.target.value)}
                          disabled={reviewStatus === "unpaid"}
                        />
                      </label>
                      <div className="rounded-md border border-line bg-warm px-3 py-2 text-sm">
                        <div className="flex justify-between gap-4">
                          <span className="text-muted">Outstanding after review</span>
                          <span className="font-semibold">{inr(String(reviewOutstandingAmount))}</span>
                        </div>
                      </div>
                    </div>
                    <textarea
                      className="mt-3 min-h-20 w-full resize-none rounded-md border border-line px-3 py-2 text-sm outline-none transition focus:border-teal focus:ring-2 focus:ring-teal/15"
                      placeholder="Optional review note"
                      value={reviewNote}
                      onChange={(event) => setReviewNote(event.target.value)}
                    />
                    <div className="mt-3 grid gap-2">
                      <SecondaryButton tone="success" onClick={() => handleReview("approve")} disabled={busy !== null}>
                        <CheckCircle2 className="h-4 w-4" />
                        Approve
                      </SecondaryButton>
                      <SecondaryButton
                        onClick={() => handleReview("correct")}
                        disabled={busy !== null || !detail.candidates.length || !selectedCandidate}
                      >
                        <FileCheck2 className="h-4 w-4" />
                        Correct match
                      </SecondaryButton>
                      <SecondaryButton tone="danger" onClick={() => handleReview("reject")} disabled={busy !== null}>
                        <XCircle className="h-4 w-4" />
                        Reject
                      </SecondaryButton>
                    </div>
                  </div>

                  <div className="rounded-lg border border-line p-4">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 text-amber" />
                      <h3 className="font-semibold">Flags</h3>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {visibleFlags().map((flag) => (
                        <span key={flag} className="rounded-full border border-line bg-warm px-2.5 py-1 text-xs text-muted">
                          {flag.replaceAll("_", " ")}
                        </span>
                      ))}
                      {!visibleFlags().length ? <p className="text-sm text-muted">No extraction flags for this record.</p> : null}
                    </div>
                  </div>

                  <div className="rounded-lg border border-line p-4">
                    <h3 className="font-semibold">Audit trail</h3>
                    <div className="mt-3 space-y-3">
                      {detail.reviews.map((review) => (
                        <div key={review.id} className="rounded-md bg-warm p-3 text-sm">
                          <p className="font-medium">
                            {review.action} - {statusLabels[review.final_status] || review.final_status}
                          </p>
                          {review.final_matched_amount || review.final_outstanding_amount ? (
                            <p className="mt-1 text-xs text-muted">
                              Paid {inr(review.final_matched_amount)} - outstanding{" "}
                              {inr(review.final_outstanding_amount)}
                            </p>
                          ) : null}
                          <p className="text-xs text-muted">{shortDate(review.created_at)}</p>
                          {review.note ? <p className="mt-1 text-muted">{review.note}</p> : null}
                        </div>
                      ))}
                      {!detail.reviews.length ? <p className="text-sm text-muted">No review decision saved yet.</p> : null}
                    </div>
                  </div>
                </aside>
              </div>
            ) : (
              <div className="py-12 text-center text-sm text-muted">
                Reload the sample workspace or upload files to inspect evidence.
              </div>
            )}
          </section>
        </section>
      </main>

      {!embedded && <footer className="mx-auto max-w-[1440px] px-5 pb-6 text-xs text-muted">
        API: {API_BASE}. FinGuard does not provide tax, legal, GST, or banking advice.
      </footer>}
    </div>
  );
}

