import {
  Activity, ArrowRight, BadgeCheck, Banknote, Check, ChevronRight, CircleAlert,
  Database, Download, ExternalLink, FileClock, FileSearch, FileText,
  FolderOpen, LayoutDashboard, Play, RotateCcw, Search,
  ShieldCheck, UploadCloud, X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import PaymentWorkspace from "./App";
import { exportUrl, fetchSummary, loadDemo } from "./api";
import type { Summary } from "./types";
import {
  approvalExportUrl, fetchApprovalDetail, fetchApprovalRows, fetchApprovalRun,
  fetchApprovalRuns, fetchApprovalSummary, fetchDemoCatalog, fetchPurchaseOrders, sourcePreviewUrl, sourceUrl,
  startApproval, submitApprovalReview, uploadProcurement
} from "./approvalApi";
import type {
  ApprovalDetail, ApprovalRow, ApprovalRun, ApprovalSummary, DemoCatalog,
  InvoiceFields, PurchaseOrder, StageEvent
} from "./approvalApi";

type Page = "overview" | "approval" | "samples" | "reconciliation" | "history" | "reports" | "review" | "run";

const stageKeys = [
  "document_received", "text_extraction", "field_extraction", "data_validation",
  "vendor_verification", "po_matching", "duplicate_detection",
  "amount_tax_validation", "decision_generation", "result_persisted"
];
const stageLabels: Record<string, string> = {
  document_received: "Document received", text_extraction: "PDF text / OCR",
  field_extraction: "Invoice fields", data_validation: "Data validation",
  vendor_verification: "Vendor verification", po_matching: "PO matching",
  duplicate_detection: "Duplicate detection", amount_tax_validation: "Amount, tax & line items",
  decision_generation: "Decision generated", result_persisted: "Result persisted"
};
const nav: Array<{ id: Page; label: string; icon: typeof LayoutDashboard }> = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "approval", label: "Invoice approval", icon: ShieldCheck },
  { id: "reconciliation", label: "Payment reconciliation", icon: Banknote },
  { id: "history", label: "Processing history", icon: FileClock },
  { id: "reports", label: "Reports", icon: Download }
];

const statusText: Record<string, string> = {
  approved: "Approved", needs_review: "Needs review", rejected: "Rejected",
  matched: "Paid", partially_paid: "Partial", unpaid: "Unpaid", unmatched_bank: "Unmatched bank"
};

function rupees(value?: string | null) {
  const number = Number(value || 0);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number.isFinite(number) ? number : 0);
}

function dateLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(date);
}

function timeLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function StatusBadge({ status }: { status?: string | null }) {
  const tone = status === "approved" || status === "matched" || status === "completed"
    ? "border-emerald/30 bg-emerald/10 text-emerald"
    : status === "rejected" || status === "failed"
      ? "border-rose/30 bg-rose/10 text-rose"
      : status === "needs_review" || status === "partially_paid"
        ? "border-amber/30 bg-amber/10 text-amber"
        : "border-line bg-warm text-muted";
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold ${tone}`}>{statusText[status || ""] || (status || "Pending")}</span>;
}

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-[#0d1b2d] shadow-panel ${className}`}>{children}</section>;
}

function Stat({ label, value, note, icon: Icon }: { label: string; value: string; note?: string; icon: typeof Activity }) {
  return <Panel className="p-5">
    <div className="flex items-start justify-between gap-3"><span className="text-xs font-semibold uppercase tracking-[0.12em] text-muted">{label}</span><Icon className="h-4 w-4 text-teal" /></div>
    <p className="mt-3 text-3xl font-semibold tracking-tight text-ink">{value}</p>
    {note && <p className="mt-2 text-xs text-muted">{note}</p>}
  </Panel>;
}

function Button({ children, onClick, disabled, kind = "primary", type = "button" }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean;
  kind?: "primary" | "secondary" | "danger"; type?: "button" | "submit";
}) {
  const tone = kind === "primary" ? "border-teal bg-teal text-[#06131d] hover:bg-[#4ee4d3]" : kind === "danger" ? "border-rose/40 bg-rose/10 text-rose hover:border-rose" : "border-line bg-[#14243a] text-ink hover:border-teal/60";
  return <button type={type} onClick={onClick} disabled={disabled} className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${tone}`}>{children}</button>;
}

function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="px-5 py-14 text-center"><FolderOpen className="mx-auto h-8 w-8 text-muted" /><p className="mt-3 font-semibold text-ink">{title}</p><p className="mx-auto mt-1 max-w-lg text-sm text-muted">{detail}</p></div>;
}

function StageOutput({ output }: { output: Record<string, unknown> }) {
  const entries = Object.entries(output).filter(([, value]) => value !== null && value !== "" && (!Array.isArray(value) || value.length > 0));
  if (!entries.length) return null;
  return <div className="mt-2 rounded-lg border border-line bg-[#091625] px-3 py-2 text-xs leading-5 text-muted">
    {entries.map(([key, value]) => key === "checks" && Array.isArray(value)
      ? <div key={key}><p className="font-semibold text-[#b8ccdc]">Validation checks</p><ul className="mt-1 space-y-1">{value.map((item, index) => {
        const check = item as { code: string; outcome: string; explanation: string };
        return <li key={index}><span className={check.outcome === "pass" ? "text-teal" : check.outcome === "fail" ? "text-rose" : "text-amber"}>{check.outcome.toUpperCase()}</span> · {check.explanation}</li>;
      })}</ul></div>
      : <p key={key}><span className="font-semibold text-[#b8ccdc]">{key.replaceAll("_", " ")}: </span>{Array.isArray(value) ? value.join(" · ") : typeof value === "object" ? JSON.stringify(value) : String(value)}</p>)}
  </div>;
}

function StageTimeline({ run }: { run: ApprovalRun | null }) {
  const events = run?.events || [];
  const latest = new Map<string, StageEvent>();
  events.forEach(event => latest.set(event.stage, event));
  return <div className="space-y-2">
    {stageKeys.map((stage, index) => {
      const current = latest.get(stage);
      const done = current?.status === "completed";
      const failed = current?.status === "failed";
      return <div key={stage} className={`rounded-xl border px-4 py-3 ${failed ? "border-rose/40 bg-rose/5" : done ? "border-line bg-[#102237]" : current ? "border-teal/40 bg-teal/5" : "border-line/60 bg-[#0a1727]"}`}>
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${done ? "bg-teal text-[#06131d]" : failed ? "bg-rose/20 text-rose" : "border border-line text-muted"}`}>{done ? <Check className="h-4 w-4" /> : failed ? <X className="h-4 w-4" /> : index + 1}</div>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold text-ink">{stageLabels[stage]}</h3><span className="text-xs text-muted">{current ? `${current.status} · ${timeLabel(current.created_at)}` : "Pending"}</span></div>
            {current && <p className="mt-1 text-sm leading-6 text-muted">{current.explanation}</p>}
            {current && <StageOutput output={current.output || {}} />}
          </div>
        </div>
      </div>;
    })}
  </div>;
}

function SampleGuide({ catalog, openApproval, openPayment }: {
  catalog: DemoCatalog | null;
  openApproval: () => void;
  openPayment: () => void;
}) {
  if (!catalog) return <Empty title="Loading sample documents" detail="Reading the included files and current workspace state." />;
  const preloaded = catalog.invoices.filter(item => item.preloaded);
  const waiting = catalog.invoices.filter(item => !item.preloaded);
  const renderDocument = (item: DemoCatalog["invoices"][number], index?: number) => <div key={item.filename} className="flex flex-col gap-3 border-b border-line px-5 py-4 last:border-b-0 sm:flex-row sm:items-start sm:justify-between">
    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-bold text-teal">{index === undefined ? "READY" : String(index + 1).padStart(2, "0")}</span><h3 className="font-semibold text-ink">{item.title}</h3></div><p className="mt-1 break-all font-mono text-xs text-[#b8c8d9]">{item.filename}</p><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{item.scenario}</p><p className="mt-2 text-xs text-muted">{item.folder} · Upload on {item.upload_page}</p></div>
    <span className={`w-fit shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${item.processed ? "border-emerald/30 bg-emerald/10 text-emerald" : "border-line bg-[#14243a] text-muted"}`}>{item.processed ? "Processed" : "Waiting to upload"}</span>
  </div>;
  return <div className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Recording guide</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Sample documents</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">These entries come from the included files and the current workspace. Use the existing file choosers to demonstrate real processing.</p></div><Button kind="secondary" onClick={openApproval}><ArrowRight className="h-4 w-4" /> Invoice approval</Button></div>
    <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]"><Panel className="h-fit overflow-hidden"><div className="border-b border-line p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">Opening workspace</p><h2 className="mt-1 text-xl font-semibold">Already loaded</h2><p className="mt-2 text-sm text-muted">These three PDFs run through the backend when the demo is reset.</p></div>{preloaded.map(item => renderDocument(item))}</Panel><Panel className="overflow-hidden"><div className="border-b border-line p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">Live sequence</p><h2 className="mt-1 text-xl font-semibold">Upload during the video</h2><p className="mt-2 text-sm text-muted">Upload the second shared-PO invoice before the over-balance case. Import the bank statement before the duplicate to show unambiguous full payments.</p></div>
      {waiting.slice(0, 3).map((item, index) => renderDocument(item, index))}
      <div className="flex flex-col gap-3 border-b border-line bg-[#102237] px-5 py-4 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-bold text-teal">04</span><h3 className="font-semibold">{catalog.bank_statement.title}</h3></div><p className="mt-1 font-mono text-xs text-[#b8c8d9]">{catalog.bank_statement.filename}</p><p className="mt-2 text-sm leading-6 text-muted">{catalog.bank_statement.scenario}</p><p className="mt-2 text-xs text-muted">{catalog.bank_statement.folder} · Upload on {catalog.bank_statement.upload_page} · {catalog.bank_statement.processed ? "Already imported" : "Waiting to upload"}</p></div><Button kind="secondary" onClick={openPayment}><Banknote className="h-4 w-4" /> Open payment</Button></div>
      {waiting.slice(3).map((item, index) => renderDocument(item, index + 4))}
    </Panel></div>
  </div>;
}

export default function LifecycleApp() {
  const [page, setPage] = useState<Page>("overview");
  const [approvalSummary, setApprovalSummary] = useState<ApprovalSummary | null>(null);
  const [paymentSummary, setPaymentSummary] = useState<Summary | null>(null);
  const [invoices, setInvoices] = useState<ApprovalRow[]>([]);
  const [runs, setRuns] = useState<ApprovalRun[]>([]);
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [catalog, setCatalog] = useState<DemoCatalog | null>(null);
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [activeRun, setActiveRun] = useState<ApprovalRun | null>(null);
  const [batchRuns, setBatchRuns] = useState<Array<{ run_id: string; filename: string }>>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [files, setFiles] = useState<File[]>([]);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [vendorCsv, setVendorCsv] = useState<File | null>(null);
  const [poCsv, setPoCsv] = useState<File | null>(null);
  const [draft, setDraft] = useState<Partial<InvoiceFields>>({});
  const [selectedPo, setSelectedPo] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    const [approval, payment, invoiceList, runList, orderList, sampleList] = await Promise.all([
      fetchApprovalSummary(), fetchSummary(), fetchApprovalRows(), fetchApprovalRuns(), fetchPurchaseOrders(), fetchDemoCatalog()
    ]);
    setApprovalSummary(approval);
    setPaymentSummary(payment);
    setInvoices(invoiceList.invoices);
    setRuns(runList.runs);
    setOrders(orderList.purchase_orders);
    setCatalog(sampleList);
  }

  useEffect(() => { refresh().catch(err => setError(err instanceof Error ? err.message : "Could not load workspace.")); }, []);
  useEffect(() => { fetchApprovalRows(search, filter).then(data => setInvoices(data.invoices)).catch(err => setError(err.message)); }, [search, filter]);
  useEffect(() => {
    if (!activeRun?.id || (activeRun.status === "completed" || activeRun.status === "failed")) return;
    const id = activeRun.id;
    const timer = window.setInterval(() => fetchApprovalRun(id).then(run => {
      setActiveRun(run);
      if (run.status === "completed" || run.status === "failed") refresh().catch(() => undefined);
    }).catch(err => setError(err.message)), 900);
    return () => window.clearInterval(timer);
  }, [activeRun?.id, activeRun?.status]);

  async function openInvoice(id: string) {
    setBusy("detail"); setError("");
    try {
      const item = await fetchApprovalDetail(id);
      setDetail(item); setDraft({ ...item.extraction, ...(item.reviews[0]?.corrections || {}) }); setSelectedPo(item.selected_po || item.matched_po || "");
      setReviewNote(""); setPage("review"); window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) { setError(err instanceof Error ? err.message : "Invoice could not be opened."); }
    finally { setBusy(null); }
  }

  async function openRun(id: string) {
    setBusy("run"); setError("");
    try { setActiveRun(await fetchApprovalRun(id)); setPage("run"); window.scrollTo({ top: 0, behavior: "smooth" }); }
    catch (err) { setError(err instanceof Error ? err.message : "Run could not be opened."); }
    finally { setBusy(null); }
  }

  async function reloadDemo() {
    setBusy("demo"); setError(""); setNotice("");
    try {
      const response = await loadDemo();
      setDetail(null); setActiveRun(null); setBatchRuns([]); await refresh();
      setFiles([]); setFileInputKey(value => value + 1);
      setNotice(response.message); setPage("overview");
    } catch (err) { setError(err instanceof Error ? err.message : "Demo could not be loaded."); }
    finally { setBusy(null); }
  }

  async function processFiles() {
    if (!files.length) return;
    setBusy("process"); setError(""); setNotice("");
    try {
      const result = await startApproval(files);
      setBatchRuns(result.runs); setFiles([]); setFileInputKey(value => value + 1);
      if (result.runs[0]) await openRun(result.runs[0].run_id);
      setNotice(`${result.runs.length} invoice processing run${result.runs.length === 1 ? "" : "s"} started.`);
    } catch (err) { setError(err instanceof Error ? err.message : "Processing could not start."); }
    finally { setBusy(null); }
  }

  async function importCsvs() {
    if (!vendorCsv && !poCsv) return;
    setBusy("procurement"); setError("");
    try {
      const result = await uploadProcurement(vendorCsv, poCsv);
      setVendorCsv(null); setPoCsv(null); await refresh();
      setNotice(`Imported ${result.vendors} vendors and ${result.purchase_orders} purchase orders.`);
    } catch (err) { setError(err instanceof Error ? err.message : "Procurement import failed."); }
    finally { setBusy(null); }
  }

  async function review(action: "approve" | "reject") {
    if (!detail) return;
    const corrections: Record<string, unknown> = {};
    for (const key of ["vendor_name", "gstin", "invoice_number", "invoice_date", "due_date", "subtotal", "tax_amount", "total_amount", "currency"] as const) {
      if ((draft[key] ?? "") !== (detail.extraction[key] ?? "")) corrections[key] = draft[key] || null;
    }
    setBusy("review"); setError("");
    try {
      const response = await submitApprovalReview(detail.invoice_id, { action, corrections,
        selected_po: selectedPo || null, note: reviewNote });
      setNotice(`${response.final_status === "approved" ? "Approval" : "Rejection"} saved to the audit trail.`);
      await refresh(); await openInvoice(detail.invoice_id);
    } catch (err) { setError(err instanceof Error ? err.message : "Review could not be saved."); }
    finally { setBusy(null); }
  }

  const activePo = useMemo(() => orders.find(po => po.number === selectedPo), [orders, selectedPo]);
  const approvalCounts = approvalSummary?.status_breakdown || { approved: 0, needs_review: 0, rejected: 0 };
  const paymentCounts = paymentSummary?.status_breakdown || {};

  return <div className="min-h-screen bg-[#06111f] text-ink">
    <div className="border-b border-line bg-[#0a1728]/95">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4 px-5 py-4 lg:px-8">
        <button onClick={() => setPage("overview")} className="flex items-center gap-3 text-left">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal text-[#07131e]"><ShieldCheck className="h-6 w-6" /></span>
          <span><span className="block text-xl font-bold tracking-tight">FinGuard<span className="text-teal">.</span></span><span className="block text-xs text-muted">Invoice lifecycle workspace</span></span>
        </button>
        <div className="flex items-center gap-2 rounded-full border border-line bg-[#102137] px-3 py-1.5 text-xs font-medium text-muted"><span className="h-2 w-2 rounded-full bg-teal" /> Local evaluation workspace</div>
      </div>
      <nav className="mx-auto flex max-w-[1500px] gap-1 overflow-x-auto px-5 pb-3 lg:px-8" aria-label="Primary navigation">
        {nav.map(item => <button key={item.id} onClick={() => setPage(item.id)} className={`flex shrink-0 items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition ${page === item.id || ((page === "review" || page === "samples") && item.id === "approval") || (page === "run" && item.id === "history") ? "bg-teal/15 text-teal" : "text-muted hover:bg-[#152840] hover:text-ink"}`}><item.icon className="h-4 w-4" />{item.label}</button>)}
      </nav>
    </div>

    <main className="mx-auto max-w-[1500px] px-5 py-7 lg:px-8">
      {(error || notice) && <div role={error ? "alert" : "status"} className={`mb-5 flex items-start gap-2 rounded-xl border px-4 py-3 text-sm ${error ? "border-rose/40 bg-rose/10 text-rose" : "border-teal/30 bg-teal/10 text-teal"}`}>{error ? <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" /> : <Check className="mt-0.5 h-4 w-4 shrink-0" />}{error || notice}</div>}

      {page === "overview" && <div className="space-y-6">
        <div className="grid gap-5 lg:grid-cols-[1.65fr_1fr]">
          <Panel className="relative overflow-hidden p-7 lg:p-9">
            <div className="absolute right-0 top-0 h-48 w-48 rounded-full bg-teal/10 blur-3xl" />
            <p className="relative text-xs font-bold uppercase tracking-[0.22em] text-teal">Accounts payable operations</p>
            <h1 className="relative mt-3 max-w-2xl text-3xl font-semibold leading-tight tracking-tight md:text-4xl">From invoice PDF to an explainable payment decision.</h1>
            <p className="relative mt-4 max-w-2xl text-sm leading-7 text-muted">Verify vendors and purchase orders, inspect every validation step, make the human call on exceptions, then track whether approved invoices were actually paid.</p>
            <div className="relative mt-6 flex flex-wrap gap-3"><Button onClick={() => setPage("approval")}><UploadCloud className="h-4 w-4" /> Process invoices</Button><Button kind="secondary" onClick={() => setPage("reconciliation")}><Banknote className="h-4 w-4" /> Track payments</Button></div>
          </Panel>
          <Panel className="p-6"><div className="flex items-center gap-2 text-teal"><Activity className="h-5 w-5" /><span className="text-sm font-semibold uppercase tracking-[0.12em]">Two connected decisions</span></div>
            <div className="mt-5 space-y-4"><div className="rounded-xl border border-line bg-[#102237] p-4"><span className="text-xs font-semibold text-teal">01 · APPROVAL</span><p className="mt-1 font-semibold">Should we approve this invoice?</p><p className="mt-1 text-xs leading-5 text-muted">Vendor, PO, duplicates, tax, line items and tolerance.</p></div><div className="rounded-xl border border-line bg-[#102237] p-4"><span className="text-xs font-semibold text-teal">02 · PAYMENT</span><p className="mt-1 font-semibold">Was this invoice actually paid?</p><p className="mt-1 text-xs leading-5 text-muted">Bank evidence, partial payments and outstanding value.</p></div></div>
          </Panel>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat icon={FileText} label="Invoices received" value={String(approvalSummary?.total_invoices ?? "—")} note="Processed from PDF evidence" />
          <Stat icon={BadgeCheck} label="Approved" value={String(approvalCounts.approved)} note="System or human final decision" />
          <Stat icon={CircleAlert} label="Needs review" value={String(approvalCounts.needs_review)} note={`${rupees(approvalSummary?.awaiting_review_value)} awaiting a decision`} />
          <Stat icon={Banknote} label="Outstanding" value={rupees(paymentSummary?.outstanding_amount)} note="Payment reconciliation balance" />
        </div>
        <div className="grid gap-5 lg:grid-cols-[1.5fr_1fr]">
          <Panel className="overflow-hidden"><div className="flex items-center justify-between gap-3 border-b border-line p-5"><div><p className="text-xs font-bold uppercase tracking-[0.12em] text-teal">Activity</p><h2 className="mt-1 text-xl font-semibold">Recent processing runs</h2></div><button onClick={() => setPage("history")} className="flex items-center gap-1 text-sm font-semibold text-teal hover:underline">View all <ArrowRight className="h-4 w-4" /></button></div>
            {runs.length ? <div className="divide-y divide-line">{runs.slice(0, 5).map(run => <button key={run.id} onClick={() => openRun(run.id)} className="flex w-full items-center justify-between gap-4 px-5 py-3 text-left transition hover:bg-[#142840]"><span className="min-w-0"><span className="block truncate font-medium">{run.filename}</span><span className="mt-1 block text-xs text-muted">{dateLabel(run.created_at)}</span></span><span className="flex items-center gap-2"><StatusBadge status={run.final_status || run.status} /><ChevronRight className="h-4 w-4 text-muted" /></span></button>)}</div> : <Empty title="No processing runs yet" detail="Load the demo workspace or process your first invoice PDF." />}
          </Panel>
          <Panel className="p-5">
            <p className="text-xs font-bold uppercase tracking-[0.12em] text-teal">Operational snapshot</p>
            <h2 className="mt-1 text-xl font-semibold">Approval to payment</h2>
            <div className="mt-5 space-y-3 text-sm"><div className="flex justify-between border-b border-line pb-3"><span className="text-muted">Rejected invoices</span><strong>{approvalCounts.rejected}</strong></div><div className="flex justify-between border-b border-line pb-3"><span className="text-muted">Fully paid records</span><strong>{paymentCounts.matched || 0}</strong></div><div className="flex justify-between border-b border-line pb-3"><span className="text-muted">Partial payments</span><strong>{paymentCounts.partially_paid || 0}</strong></div><div className="flex justify-between border-b border-line pb-3"><span className="text-muted">Unpaid records</span><strong>{paymentCounts.unpaid || 0}</strong></div><div className="flex justify-between"><span className="text-muted">Invoice value processed</span><strong>{rupees(approvalSummary?.total_value)}</strong></div></div>
            <div className="mt-5 flex flex-wrap gap-2"><Button kind="secondary" onClick={reloadDemo} disabled={busy === "demo"}><RotateCcw className="h-4 w-4" /> {busy === "demo" ? "Reprocessing demo…" : "Reload demo workspace"}</Button><Button kind="secondary" onClick={() => setPage("samples")}><FolderOpen className="h-4 w-4" /> Sample documents</Button></div>
            <p className="mt-3 text-xs leading-5 text-muted">Reloading restores three invoices and the procurement register. The other PDFs and bank CSV stay ready for upload.</p>
          </Panel>
        </div>
      </div>}

      {page === "approval" && <div className="space-y-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Workflow 01</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Invoice approval</h1><p className="mt-2 text-sm text-muted">Process PDFs against the vendor master and purchase order register.</p></div><div className="flex flex-wrap gap-2"><Button kind="secondary" onClick={() => setPage("samples")}><FolderOpen className="h-4 w-4" /> Sample documents</Button><Button kind="secondary" onClick={reloadDemo} disabled={busy === "demo"}><RotateCcw className="h-4 w-4" /> Reload demo</Button></div></div>
        <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]"><div className="space-y-5">
          <Panel className="p-5"><div className="flex items-center gap-2"><UploadCloud className="h-5 w-5 text-teal" /><h2 className="text-lg font-semibold">Process invoice PDFs</h2></div><p className="mt-2 text-sm leading-6 text-muted">Choose one or more PDFs. Each file gets its own real processing run and audit trail.</p><input key={fileInputKey} aria-label="Invoice PDFs" type="file" accept=".pdf,application/pdf" multiple onChange={event => setFiles(Array.from(event.target.files || []))} className="mt-4 w-full rounded-lg border border-line bg-[#081728] p-2 text-sm text-muted file:mr-3 file:rounded-md file:border-0 file:bg-[#1a3047] file:px-3 file:py-2 file:font-semibold file:text-ink" /><p className="mt-2 text-xs text-muted">{files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "PDF · up to 12 MB each"}</p><div className="mt-4"><Button onClick={processFiles} disabled={!files.length || busy === "process"}><Play className="h-4 w-4" /> {busy === "process" ? "Starting…" : "Start processing"}</Button></div></Panel>
          <Panel className="p-5"><div className="flex items-center gap-2"><Database className="h-5 w-5 text-teal" /><h2 className="text-lg font-semibold">Procurement data</h2></div><p className="mt-2 text-sm text-muted">{orders.length} purchase orders loaded. Import UTF-8 CSVs to update the register and vendor master.</p><label className="mt-4 block text-xs font-semibold uppercase tracking-wider text-muted">Vendor master CSV<input type="file" accept=".csv,text/csv" onChange={event => setVendorCsv(event.target.files?.[0] || null)} className="mt-2 block w-full text-sm normal-case tracking-normal text-ink" /></label><label className="mt-4 block text-xs font-semibold uppercase tracking-wider text-muted">PO register CSV<input type="file" accept=".csv,text/csv" onChange={event => setPoCsv(event.target.files?.[0] || null)} className="mt-2 block w-full text-sm normal-case tracking-normal text-ink" /></label><div className="mt-4"><Button kind="secondary" onClick={importCsvs} disabled={(!vendorCsv && !poCsv) || busy === "procurement"}>Import procurement data</Button></div><p className="mt-3 text-xs text-muted">Sample schemas are in <span className="font-mono">sample_data/vendors.csv</span> and <span className="font-mono">sample_data/purchase_orders.csv</span>.</p></Panel>
        </div>
        <Panel className="min-w-0 overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-4 border-b border-line p-5"><div><p className="text-xs font-bold uppercase tracking-[0.12em] text-teal">Decision queue</p><h2 className="mt-1 text-xl font-semibold">Processed invoices</h2></div><div className="flex flex-wrap gap-2"><label className="relative"><Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted" /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search vendor, invoice, PO" className="min-h-10 w-56 rounded-lg border border-line bg-[#091729] pl-9 pr-3 text-sm text-ink outline-none focus:border-teal" /></label><select aria-label="Filter approval status" value={filter} onChange={event => setFilter(event.target.value)} className="min-h-10 rounded-lg border border-line bg-[#091729] px-3 text-sm text-ink"><option value="all">All decisions</option><option value="approved">Approved</option><option value="needs_review">Needs review</option><option value="rejected">Rejected</option></select></div></div>
          {invoices.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="bg-[#172a41] text-xs uppercase tracking-wider text-[#b8c8d9]"><tr><th className="px-5 py-3">Invoice</th><th className="px-4 py-3">Date</th><th className="px-4 py-3">PO</th><th className="px-4 py-3">Amount</th><th className="px-4 py-3">Decision</th><th className="px-4 py-3">Action</th></tr></thead><tbody>{invoices.map(row => <tr key={row.invoice_id} className="border-t border-line transition hover:bg-[#152a40]"><td className="px-5 py-4"><p className="font-semibold text-ink">{row.vendor_name || "Vendor unverified"}</p><p className="mt-1 text-xs text-muted">{row.invoice_number || "Invoice number missing"} · {row.original_filename}</p></td><td className="whitespace-nowrap px-4 py-4 text-muted">{dateLabel(row.invoice_date)}</td><td className="whitespace-nowrap px-4 py-4 font-mono text-xs text-[#b8c8d9]">{row.matched_po || "Unmatched"}</td><td className="whitespace-nowrap px-4 py-4 font-semibold">{row.total_amount ? rupees(row.total_amount) : "Unverified"}</td><td className="px-4 py-4"><StatusBadge status={row.final_status} /></td><td className="px-4 py-4"><button onClick={() => openInvoice(row.invoice_id)} className="inline-flex items-center gap-1 text-sm font-semibold text-teal hover:underline">Inspect <ChevronRight className="h-4 w-4" /></button></td></tr>)}</tbody></table></div> : <Empty title="No invoices match this view" detail="Clear the filter or process invoice PDFs to populate the decision queue." />}
        </Panel></div></div>}

      {page === "samples" && <SampleGuide catalog={catalog} openApproval={() => setPage("approval")} openPayment={() => setPage("reconciliation")} />}

      {page === "run" && <div className="space-y-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Live processing</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">{activeRun?.filename || "Processing run"}</h1><p className="mt-2 text-sm text-muted">Stages below are recorded by the backend as processing happens.</p></div><div className="flex gap-2"><StatusBadge status={activeRun?.status} />{activeRun?.invoice_id && <Button kind="secondary" onClick={() => openInvoice(activeRun.invoice_id!)}><FileSearch className="h-4 w-4" /> Inspect invoice</Button>}</div></div>
        {batchRuns.length > 1 && <Panel className="p-4"><p className="mb-3 text-xs font-bold uppercase tracking-wider text-muted">Batch files</p><div className="flex flex-wrap gap-2">{batchRuns.map(item => <button key={item.run_id} onClick={() => openRun(item.run_id)} className={`rounded-lg border px-3 py-2 text-xs font-semibold ${activeRun?.id === item.run_id ? "border-teal bg-teal/10 text-teal" : "border-line text-muted hover:text-ink"}`}>{item.filename}</button>)}</div></Panel>}
        {activeRun?.error && <div className="rounded-xl border border-rose/40 bg-rose/10 p-4 text-sm text-rose">{activeRun.error}</div>}
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]"><Panel className="p-5"><StageTimeline run={activeRun} /></Panel><Panel className="h-fit p-5"><h2 className="text-lg font-semibold">Run details</h2><div className="mt-4 space-y-3 text-sm"><p className="flex justify-between gap-3"><span className="text-muted">Started</span><span>{dateLabel(activeRun?.created_at)}</span></p><p className="flex justify-between gap-3"><span className="text-muted">State</span><StatusBadge status={activeRun?.status} /></p><p className="flex justify-between gap-3"><span className="text-muted">Invoice ID</span><span className="break-all font-mono text-xs">{activeRun?.invoice_id || "Awaiting extraction"}</span></p></div><div className="mt-5 space-y-2"><Button kind="secondary" onClick={() => activeRun && openRun(activeRun.id)}><RotateCcw className="h-4 w-4" /> Refresh run</Button><Button kind="secondary" onClick={() => setPage("history")}>View history <ArrowRight className="h-4 w-4" /></Button></div></Panel></div>
      </div>}

      {page === "review" && <div className="space-y-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><button onClick={() => setPage("approval")} className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-teal hover:underline">‹ Invoice approval</button><h1 className="text-3xl font-semibold tracking-tight">{detail?.invoice_number || "Invoice review"}</h1><p className="mt-2 text-sm text-muted">{detail?.vendor_name || "Vendor unverified"} · {detail?.original_filename}</p></div><div className="flex items-center gap-2"><StatusBadge status={detail?.suggested_status} /><span className="text-xs text-muted">System suggestion</span><StatusBadge status={detail?.final_status} /><span className="text-xs text-muted">Final</span></div></div>
        {detail && <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]"><div className="space-y-5">
          <Panel className="p-5"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-wider text-teal">Original evidence</p><h2 className="mt-1 text-xl font-semibold">Source invoice</h2></div>{detail.source_file_id && <a href={sourceUrl(detail.source_file_id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm font-semibold text-teal hover:border-teal"><ExternalLink className="h-4 w-4" /> Open original PDF</a>}</div><p className="mt-2 text-xs text-muted">Extraction method: {detail.extraction_method.replaceAll("_", " ")} · Page references appear with extracted fields.</p>{detail.source_file_id && <div className="mt-4 flex max-h-[470px] justify-center overflow-y-auto rounded-lg border border-line bg-[#e8edf2] p-3"><img alt={`First page of ${detail.original_filename}`} src={sourcePreviewUrl(detail.source_file_id)} className="h-auto w-full max-w-[540px] object-contain shadow-lg" /></div>}</Panel>
          <Panel className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">System analysis</p><h2 className="mt-1 text-xl font-semibold">Decision & checks</h2><p className="mt-4 rounded-xl border border-line bg-[#102237] p-4 text-sm leading-7 text-ink">{detail.suggestion.explanation}</p>
            {detail.suggestion.candidate_pos.length > 1 && <p className="mt-3 text-sm text-amber">Candidate POs: {detail.suggestion.candidate_pos.join(", ")}. An analyst must choose the intended order.</p>}
            {detail.suggestion.duplicate_of && <button onClick={() => openInvoice(detail.suggestion.duplicate_of!)} className="mt-3 text-sm font-semibold text-teal hover:underline">Inspect earlier invoice {detail.suggestion.duplicate_of} →</button>}
            <div className="mt-4 space-y-2">{detail.suggestion.checks.map(check => <div key={check.code} className="flex items-start gap-3 rounded-lg border border-line bg-[#0a1728] p-3"><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${check.outcome === "pass" ? "bg-teal" : check.outcome === "fail" ? "bg-rose" : "bg-amber"}`} /><div><p className="text-xs font-bold uppercase tracking-wider text-[#b8c8d9]">{check.code.replaceAll("_", " ")}</p><p className="mt-1 text-sm leading-6 text-muted">{check.explanation}</p></div></div>)}</div></Panel>
          <Panel className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">Related records</p><h2 className="mt-1 text-xl font-semibold">PO and payment</h2><div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-xl border border-line bg-[#102237] p-4"><p className="text-xs text-muted">Matched purchase order</p><p className="mt-1 font-semibold">{activePo?.number || detail.matched_po || "Not established"}</p><p className="mt-2 text-xs text-muted">Approved: {activePo ? rupees(activePo.approved_amount) : "—"}</p><p className="text-xs text-muted">Remaining: {activePo ? rupees(activePo.remaining_balance) : "—"}</p>{activePo?.line_items.map((line, i) => <p key={i} className="mt-2 text-xs text-muted">{line.description}</p>)}</div><div className="rounded-xl border border-line bg-[#102237] p-4"><p className="text-xs text-muted">Payment status</p><div className="mt-2"><StatusBadge status={detail.payment?.outcome || "unpaid"} /></div><p className="mt-3 text-xs text-muted">Matched: {rupees(detail.payment?.matched_amount)}</p><p className="text-xs text-muted">Outstanding: {rupees(detail.payment?.outstanding_amount)}</p><p className="mt-2 text-xs leading-5 text-muted">{detail.payment?.reason_text || "No bank evidence linked yet."}</p><button onClick={() => setPage("reconciliation")} className="mt-3 text-xs font-semibold text-teal hover:underline">Open payment workspace →</button></div></div></Panel>
          <Panel className="p-5"><div className="flex items-center justify-between gap-2"><h2 className="text-lg font-semibold">Processing trace</h2><button onClick={() => openRun(detail.run_id)} className="text-sm font-semibold text-teal hover:underline">Open run →</button></div><p className="mt-2 text-sm text-muted">{detail.events.filter(e => e.status === "completed").length} completed stages persisted with source outputs.</p></Panel>
        </div><aside className="space-y-5">
          <Panel className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">Extracted fields</p><h2 className="mt-1 text-xl font-semibold">Analyst correction</h2><p className="mt-2 text-xs leading-5 text-muted">Edits are saved only with a human decision. The original extraction remains in the audit record.</p><div className="mt-4 space-y-3">{([
            ["vendor_name", "Vendor"], ["gstin", "GSTIN"], ["invoice_number", "Invoice number"],
            ["invoice_date", "Invoice date"], ["subtotal", "Subtotal"],
            ["tax_amount", "Tax amount"], ["total_amount", "Invoice total"]
          ] as Array<[keyof InvoiceFields, string]>).map(([key, label]) => <label key={key} className="block"><span className="mb-1 block text-xs font-semibold text-muted">{label} {detail.extraction.field_pages[key] ? `· page ${detail.extraction.field_pages[key]}` : "· source page unknown"}</span><input value={String(draft[key] ?? "")} onChange={event => setDraft(old => ({ ...old, [key]: event.target.value }))} className="min-h-10 w-full rounded-lg border border-line bg-[#091729] px-3 text-sm text-ink outline-none focus:border-teal" />{String(draft[key] ?? "") !== String(detail.extraction[key] ?? "") && <span className="mt-1 block text-xs text-amber">Original extraction: {String(detail.extraction[key] ?? "missing")}</span>}</label>)}<label className="block"><span className="mb-1 block text-xs font-semibold text-muted">Purchase order</span><select value={selectedPo} onChange={event => setSelectedPo(event.target.value)} className="min-h-10 w-full rounded-lg border border-line bg-[#091729] px-3 text-sm text-ink"><option value="">No PO selected</option>{orders.map(po => <option key={po.number} value={po.number}>{po.number} · {rupees(po.approved_amount)}</option>)}</select></label></div>{detail.extraction.warnings.length > 0 && <div className="mt-4 rounded-lg border border-amber/30 bg-amber/10 p-3 text-xs leading-5 text-amber">{detail.extraction.warnings.join(" · ")}</div>}</Panel>
          <Panel className="p-5"><p className="text-xs font-bold uppercase tracking-wider text-teal">Human authority</p><h2 className="mt-1 text-xl font-semibold">Review decision</h2><textarea value={reviewNote} onChange={event => setReviewNote(event.target.value)} placeholder="Explain your approval or rejection" className="mt-4 min-h-24 w-full resize-y rounded-lg border border-line bg-[#091729] p-3 text-sm text-ink outline-none focus:border-teal" /><div className="mt-3 grid grid-cols-2 gap-2"><Button onClick={() => review("approve")} disabled={busy === "review"}><Check className="h-4 w-4" /> Approve</Button><Button kind="danger" onClick={() => review("reject")} disabled={busy === "review"}><X className="h-4 w-4" /> Reject</Button></div><p className="mt-3 text-xs leading-5 text-muted">Approval and payment are separate. This action never marks the invoice paid.</p></Panel>
          <Panel className="p-5"><h2 className="text-lg font-semibold">Audit trail</h2><div className="mt-3 space-y-3">{detail.reviews.length ? detail.reviews.map(entry => <div key={entry.id} className="rounded-lg border border-line bg-[#102237] p-3 text-sm"><div className="flex items-center justify-between"><StatusBadge status={entry.final_status} /><span className="text-xs text-muted">{dateLabel(entry.created_at)}</span></div><p className="mt-2 text-xs text-muted">From system suggestion: {statusText[entry.previous_suggestion]}</p>{Object.entries(entry.corrections).map(([key, value]) => <p key={key} className="mt-1 text-xs text-muted">{key.replaceAll("_", " ")}: {String(detail.extraction[key as keyof InvoiceFields] ?? "missing")} → {String(value ?? "missing")}</p>)}{entry.selected_po && <p className="mt-1 text-xs text-muted">Selected PO: {entry.selected_po}</p>}{entry.note && <p className="mt-2 leading-6">{entry.note}</p>}</div>) : <p className="text-sm text-muted">No human decision recorded yet.</p>}</div></Panel>
        </aside></div>}
      </div>}

      {page === "reconciliation" && <div><div className="mb-1"><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Workflow 02</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Payment reconciliation</h1><p className="mt-2 text-sm text-muted">Match bank transactions to the same invoice records. Approval and payment are independent statuses.</p></div><div className="payment-embed -mx-5 lg:-mx-8"><PaymentWorkspace embedded onWorkspaceChange={() => { setNotice(""); return refresh(); }} /></div></div>}

      {page === "history" && <div className="space-y-5"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Traceability</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Processing history</h1><p className="mt-2 text-sm text-muted">Every uploaded PDF has a run, source document and stage events.</p></div><Panel className="overflow-hidden">{runs.length ? <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-[#172a41] text-xs uppercase tracking-wider text-[#b8c8d9]"><tr><th className="px-5 py-3">Document</th><th className="px-4 py-3">Started</th><th className="px-4 py-3">Processing</th><th className="px-4 py-3">Decision</th><th className="px-4 py-3">Inspect</th></tr></thead><tbody>{runs.map(run => <tr key={run.id} className="border-t border-line hover:bg-[#152a40]"><td className="px-5 py-4 font-semibold">{run.filename}</td><td className="px-4 py-4 text-muted">{dateLabel(run.created_at)}</td><td className="px-4 py-4"><StatusBadge status={run.status} /></td><td className="px-4 py-4"><StatusBadge status={run.final_status} /></td><td className="px-4 py-4"><button onClick={() => openRun(run.id)} className="text-sm font-semibold text-teal hover:underline">Open run →</button></td></tr>)}</tbody></table></div> : <Empty title="No run history yet" detail="Process an invoice to create a persisted stage trace." />}</Panel></div>}

      {page === "reports" && <div className="space-y-5"><div><p className="text-xs font-bold uppercase tracking-[0.14em] text-teal">Exports</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Reports</h1><p className="mt-2 text-sm text-muted">Export real decisions and reconciliation results from the local SQLite workspace.</p></div><div className="grid gap-5 md:grid-cols-2"><Panel className="p-6"><ShieldCheck className="h-7 w-7 text-teal" /><h2 className="mt-4 text-xl font-semibold">Approval decision report</h2><p className="mt-2 text-sm leading-6 text-muted">Invoice, vendor, PO, amount, difference, tolerance, validation warnings, machine suggestion and human decision.</p><a href={approvalExportUrl} className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-lg border border-teal bg-teal px-4 py-2 text-sm font-semibold text-[#06131d] hover:bg-[#4ee4d3]"><Download className="h-4 w-4" /> Download approval CSV</a></Panel><Panel className="p-6"><Banknote className="h-7 w-7 text-teal" /><h2 className="mt-4 text-xl font-semibold">Payment reconciliation report</h2><p className="mt-2 text-sm leading-6 text-muted">Suggested and reviewed payment status, matched bank amount, outstanding value, evidence and analyst notes.</p><a href={exportUrl} className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-[#14243a] px-4 py-2 text-sm font-semibold text-ink hover:border-teal"><Download className="h-4 w-4" /> Download payment CSV</a></Panel></div></div>}
    </main>
    <footer className="mx-auto max-w-[1500px] border-t border-line px-5 py-6 text-xs text-muted lg:px-8">FinGuard · Local evaluation prototype · Human review is required for exceptions and uncertain extraction.</footer>
  </div>;
}
