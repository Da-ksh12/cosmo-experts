"use client";

import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, Info, Loader2, X, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { blob, STATUS_LABEL } from "@/lib/api";

/* ───────── formatting ───────── */
export const fmtDate = (ts?: number | null) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "");
export const fmtDateTime = (ts?: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "");
export const fmtBytes = (n?: number | null) => (!n ? "" : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
export const money = (n: number, cur = "INR") => new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(n);

/* ───────── brand & decoration ───────── */
export function Leaf({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 160 160" fill="none" aria-hidden="true">
      <path d="M92 8c40 18 56 62 28 104-14 20-40 30-62 24C44 98 52 40 92 8Z" fill="#cfe0dc" opacity=".75" />
      <path d="M92 8c-10 34-18 76-34 128" stroke="#b58a45" strokeWidth="1.2" opacity=".7" />
      <path d="M76 52c14-2 26-10 34-22M68 84c18 0 32-8 42-22M62 112c16 2 32-6 44-18" stroke="#b58a45" strokeWidth="1" opacity=".5" />
      <path d="M26 128c22-26 44-24 62-10-6 22-34 34-62 10Z" fill="#9cbab3" opacity=".7" />
    </svg>
  );
}

export function BrandLogo({ compact = false, small = false }: { compact?: boolean; small?: boolean }) {
  return (
    <span className={`inline-flex min-w-0 flex-col leading-none md:whitespace-nowrap`} aria-label="Cosmo Experts AI">
      <span className={`font-serif font-bold text-teal-dark ${small ? "text-xl" : "text-[1.55rem]"}`}>Cosmo Experts</span>
      {!compact && <span className={`mt-1 font-semibold text-[#86622a] ${small ? "text-[.5rem] tracking-[.12em]" : "text-[.6rem] tracking-[.22em]"}`}>SKIN | HAIR | BODY | AESTHETICS</span>}
    </span>
  );
}

export function PageHeading({ eyebrow, title, subtitle, art = true }: { eyebrow?: string; title: string; subtitle?: string; art?: boolean }) {
  return (
    <header className="relative rise break-words pr-16 sm:pr-40">
      {art && <Leaf className="pointer-events-none absolute -right-[12px] -top-4 h-28 w-28 sm:h-40 sm:w-40" />}
      {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
      <h1>{title}</h1>
      {subtitle && <p className="mt-2 max-w-xl text-muted">{subtitle}</p>}
    </header>
  );
}

export function ProgressSteps({ step, total }: { step: number; total: number }) {
  return (
    <div className="flex flex-col items-end gap-1" role="img" aria-label={`Step ${step} of ${total}`}>
      <div className="flex items-center">
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className="flex items-center">
            <span className={`h-2.5 w-2.5 rounded-full ${i < step ? "bg-teal-dark" : "bg-line"}`} />
            {i < total - 1 && <span className={`h-0.5 w-6 ${i < step - 1 ? "bg-teal-dark" : "bg-line"}`} />}
          </span>
        ))}
      </div>
      <span className="text-sm text-muted">Step {step} of {total}</span>
    </div>
  );
}

/* ───────── status ───────── */
const TONE: Record<string, string> = {
  available: "note-good", research_only: "note-warn", clinician_review_required: "note-info", unavailable: "note-bad", approved_for_defined_use: "note-good",
};
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <span className={`badge ${TONE[status] ?? "note-info"}`}>{label ?? STATUS_LABEL[status] ?? status}</span>;
}
export function Pill({ tone = "info", children }: { tone?: "good" | "warn" | "bad" | "info" | "gold"; children: React.ReactNode }) {
  return <span className={`badge note-${tone}`}>{children}</span>;
}

const NOTE_ICON = { info: Info, warn: AlertTriangle, bad: XCircle, ok: CheckCircle2, good: CheckCircle2, gold: Info } as const;
export function Note({ tone = "info", children, className = "" }: { tone?: keyof typeof NOTE_ICON; children: React.ReactNode; className?: string }) {
  const Icon = NOTE_ICON[tone];
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={`note note-${tone} ${className}`}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/* ───────── states ───────── */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return <div role="status" className="flex items-center justify-center gap-3 py-16 text-muted"><Loader2 className="spin h-5 w-5" aria-hidden />{label}</div>;
}
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card text-center" role="alert">
      <XCircle className="mx-auto mb-2 h-8 w-8 text-[var(--bad-ink)]" aria-hidden />
      <p className="font-semibold">{message}</p>
      {onRetry && <button className="btn mt-3" onClick={onRetry}>Try again</button>}
    </div>
  );
}
export function EmptyState({ icon, title, children, action }: { icon: React.ReactNode; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="card-flat flex flex-col items-center gap-2 py-10 text-center">
      <span className="grid h-14 w-14 place-items-center rounded-full bg-sage text-teal">{icon}</span>
      <h3>{title}</h3>
      {children && <p className="max-w-md text-muted">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Details({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="card-flat group">
      <summary className="flex items-center justify-between font-semibold text-teal-dark">{title}<span className="text-muted transition group-open:rotate-90" aria-hidden>›</span></summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

/* ───────── dialog + confirm + toast ───────── */
export function Dialog({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button, [href], input, select, textarea")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab" || !ref.current) return;
      // keep keyboard focus inside the modal: wrap at both ends
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1], active = document.activeElement;
      if (!ref.current.contains(active)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; prev?.focus(); };
  }, [onClose]);
  // Portal to <body>: an ancestor with backdrop-filter/transform (the sticky header) would otherwise become the containing block of this fixed overlay.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className={`pop max-h-[92dvh] w-full overflow-auto rounded-t-[28px] bg-ivory p-5 sm:rounded-[28px] ${wide ? "sm:max-w-3xl" : "sm:max-w-md"}`}>
        <div className="mb-3 flex items-center justify-between gap-3"><h2>{title}</h2>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close"><X className="h-5 w-5" /></button></div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({ title, message, confirmLabel = "Confirm", danger = false, onConfirm, onCancel }:
  { title: string; message: string; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onCancel: () => void }) {
  return (
    <Dialog title={title} onClose={onCancel}>
      <p className="mb-5 text-muted">{message}</p>
      <div className="flex justify-end gap-3"><button className="btn" onClick={onCancel}>Cancel</button>
        <button className={`btn ${danger ? "" : "btn-primary"}`} style={danger ? { background: "var(--bad-ink)", color: "#fff", borderColor: "var(--bad-ink)" } : undefined} onClick={onConfirm}>{confirmLabel}</button></div>
    </Dialog>
  );
}

type Toast = { id: number; tone: "ok" | "bad" | "info"; text: string };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setItems((i) => [...i, { id, tone, text }]);
    setTimeout(() => setItems((i) => i.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed left-1/2 z-[60] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2" style={{ bottom: "calc(var(--nav-h) + 16px)" }} aria-live="polite">
        {items.map((t) => <div key={t.id} className={`pop note ${t.tone === "bad" ? "note-bad" : t.tone === "info" ? "note-info" : "note-good"} shadow-lg`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

/* ───────── authenticated image ───────── */
export function AuthImage({ src, alt, className = "", fallback, blur = false }: { src: string; alt: string; className?: string; fallback?: React.ReactNode; blur?: boolean }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true; let made = "";
    blob(src).then((b) => { if (!live) return; made = URL.createObjectURL(b); setUrl(made); }).catch(() => live && setFailed(true));
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [src]);
  if (failed) return <div className={`grid place-items-center bg-sage text-teal ${className}`}>{fallback}</div>;
  if (!url) return <div className={`animate-pulse bg-line ${className}`} aria-hidden />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt={alt} className={`${className} ${blur ? "blur-xl" : ""} object-cover transition`} />;
}
