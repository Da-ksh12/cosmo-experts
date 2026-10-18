"use client";

import { CalendarDays, Check, Clock, Download, Info, Minus, Plus, Send, Share2, Sparkles, Stethoscope, Tag } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { EmptyState, ErrorState, LoadingState, Note, PageHeading, Pill, money, useToast } from "@/components/ui";
import { api, blob, post } from "@/lib/api";

type Price = { id: string; name: string; description: string; unit_price: number; currency: string; category: string; sessions: number; duration_min: number; compare_at_price: number | null };
type Cfg = { tax_percent: number; payment_options: string[]; inclusions: string[]; terms: string };
type Quote = { id: string; status: string; total: number; subtotal: number; tax_amount: number; currency: string; lines: { name: string; qty: number }[]; created_at: number };
type Care = { plan: { recommended_item_ids: string[]; notes: string } | null; doctor: { name: string } | null };

const r2 = (n: number) => Math.round(n * 100) / 100;

export default function QuotePage() {
  const toast = useToast();
  const [prices, setPrices] = useState<Price[] | null>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [care, setCare] = useState<Care | null>(null);
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [err, setErr] = useState("");
  const [cat, setCat] = useState("");
  const [sel, setSel] = useState<Record<string, number>>({});
  const [pay, setPay] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([api<Price[]>("/api/price-items"), api<Cfg>("/api/quote-config"), api<Care>("/api/care-plan"), api<Quote[]>("/api/quotes")])
      .then(([p, c, cp, q]) => { setErr(""); setPrices(p); setCfg(c); setCare(cp); setQuotes(q); }).catch((e) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const rec = useMemo(() => new Set(care?.plan?.recommended_item_ids ?? []), [care]);
  const cats = useMemo(() => [...new Set((prices ?? []).map((p) => p.category))], [prices]);
  const tabs = useMemo(() => [...(rec.size ? [["rec", "Recommended for You"]] : []), ["all", "All Treatments"], ...cats.map((c) => [c, c])] as [string, string][], [rec, cats]);
  const activeCat = cat || (rec.size ? "rec" : "all");

  if (err) return <ErrorState message={err} onRetry={load} />;
  if (!prices || !cfg) return <LoadingState />;

  const shown = prices.filter((p) => activeCat === "rec" ? rec.has(p.id) : activeCat === "all" ? true : p.category === activeCat);
  const chosen = prices.filter((p) => (sel[p.id] ?? 0) > 0);
  const subtotal = r2(chosen.reduce((s, p) => s + r2(p.unit_price * sel[p.id]), 0));
  const tax = r2((subtotal * cfg.tax_percent) / 100);
  const total = r2(subtotal + tax);
  const cur = chosen[0]?.currency ?? prices[0]?.currency ?? "INR";
  const mixed = new Set(chosen.map((p) => p.currency)).size > 1;

  const toggle = (p: Price) => setSel((s) => ({ ...s, [p.id]: s[p.id] ? 0 : p.sessions }));
  const step = (p: Price, d: number) => setSel((s) => ({ ...s, [p.id]: Math.max(1, Math.min(100, (s[p.id] || p.sessions) + d)) }));

  async function submit() {
    setBusy(true);
    try {
      await post("/api/quotes", { items: chosen.map((p) => ({ item_id: p.id, qty: sel[p.id] })), notes: pay ? `Payment preference: ${pay}` : "" });
      toast("Sent. The clinic will review your estimate and approve a quotation."); setSel({}); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Could not send your request.", "bad"); }
    finally { setBusy(false); }
  }
  async function pdf(q: Quote, share: boolean) {
    try {
      const b = await blob(`/api/quotes/${q.id}/pdf`);
      const file = new File([b], `quotation-${q.id.slice(0, 8)}.pdf`, { type: "application/pdf" });
      if (share && navigator.canShare?.({ files: [file] })) { await navigator.share({ files: [file], title: "Cosmo Experts quotation" }); return; }
      const a = document.createElement("a"); a.href = URL.createObjectURL(file); a.download = file.name; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { if ((e as Error).name !== "AbortError") toast("Could not prepare the PDF.", "bad"); }
  }
  const canShare = typeof navigator !== "undefined" && "share" in navigator;

  return (
    <div className="space-y-6">
      <ScreenBar back="/care" />
      <PageHeading eyebrow="Your care plan" title="Treatment Quotation Builder" subtitle="Choose items from the clinic's price list to see an estimate. Your doctor decides what is right for you." />

      {prices.length === 0 ? (
        <EmptyState icon={<Tag className="h-7 w-7" />} title="No price list yet">The clinic hasn&apos;t published its approved price list, so no estimate can be built. Please ask the clinic team.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="space-y-4">
            <div className="scroll-x" role="tablist" aria-label="Treatment categories">{tabs.map(([id, label]) => <button key={id} role="tab" aria-selected={activeCat === id} className="chip" onClick={() => setCat(id)}>{id === "rec" && <Sparkles className="h-4 w-4" aria-hidden />}{label}</button>)}</div>
            {care?.plan && rec.size > 0 && <p className="flex items-center gap-2 text-sm text-muted"><Stethoscope className="h-4 w-4 text-gold" aria-hidden /> Recommended items come from your clinician-approved care plan.</p>}
            <ul className="space-y-3">
              {shown.map((p) => {
                const on = (sel[p.id] ?? 0) > 0, q = sel[p.id] || p.sessions;
                return (
                  <li key={p.id} className={`card flex gap-3 !p-4 transition ${on ? "!border-teal bg-teal-soft/40" : ""}`}>
                    <input type="checkbox" className="mt-1 h-6 w-6 shrink-0 accent-[var(--teal)]" checked={on} onChange={() => toggle(p)} aria-label={`Add ${p.name} to quotation`} />
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2"><h3 className="font-sans !text-lg">{p.name}</h3>{rec.has(p.id) && <Pill tone="gold">Recommended by your clinician</Pill>}</div>
                      {p.description && <p className="text-sm text-muted">{p.description}</p>}
                      <p className="flex flex-wrap gap-x-4 text-sm text-muted"><span className="inline-flex items-center gap-1"><CalendarDays className="h-4 w-4" aria-hidden />{q} session{q === 1 ? "" : "s"}</span>
                        {p.duration_min > 0 && <span className="inline-flex items-center gap-1"><Clock className="h-4 w-4" aria-hidden />{p.duration_min} mins / session</span>}</p>
                      {on && <div className="flex items-center gap-2 pt-1"><span className="text-sm">Sessions</span>
                        <button className="btn btn-icon btn-sm" onClick={() => step(p, -1)} aria-label={`Fewer sessions of ${p.name}`}><Minus className="h-4 w-4" /></button><span className="w-8 text-center font-semibold" aria-live="polite">{q}</span>
                        <button className="btn btn-icon btn-sm" onClick={() => step(p, 1)} aria-label={`More sessions of ${p.name}`}><Plus className="h-4 w-4" /></button></div>}
                    </div>
                    <div className="text-right"><p className="text-lg font-semibold">{money(p.unit_price, p.currency)}</p>
                      {p.compare_at_price ? <p className="text-sm text-muted line-through">{money(p.compare_at_price, p.currency)}</p> : null}<p className="text-xs text-muted">per session</p></div>
                  </li>);
              })}
              {shown.length === 0 && <li><EmptyState icon={<Tag className="h-7 w-7" />} title="Nothing in this category" /></li>}
            </ul>
            {care?.plan?.notes && rec.size > 0 && <section className="card-sage flex gap-3"><Stethoscope className="h-8 w-8 shrink-0 text-teal" strokeWidth={1.4} aria-hidden /><div><h3>Doctor&apos;s Advice</h3><p className="whitespace-pre-wrap text-sm">{care.plan.notes}</p></div></section>}
          </div>

          <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start" aria-label="Your quotation">
            <section className="card space-y-3">
              <div className="flex items-center justify-between"><h2>Your Estimate</h2><Pill tone="warn">Estimate only</Pill></div>
              {chosen.length === 0 ? <p className="text-muted">Select items to build your estimate.</p> : (
                <ul className="divide-y divide-line">{chosen.map((p) => <li key={p.id} className="flex justify-between gap-3 py-2"><span><span className="block font-semibold">{p.name}</span><span className="text-sm text-muted">{sel[p.id]} session{sel[p.id] === 1 ? "" : "s"}</span></span><span className="font-semibold">{money(r2(p.unit_price * sel[p.id]), p.currency)}</span></li>)}</ul>)}
              <dl className="space-y-1 border-t border-line pt-3">
                {cfg.tax_percent > 0 && <><div className="flex justify-between text-muted"><dt>Subtotal</dt><dd>{money(subtotal, cur)}</dd></div><div className="flex justify-between text-muted"><dt>Tax ({cfg.tax_percent}%)</dt><dd>{money(tax, cur)}</dd></div></>}
                <div className="flex justify-between text-xl font-bold text-teal-dark"><dt className="font-serif">Total Amount</dt><dd aria-live="polite" className="font-sans tabular-nums">{money(total, cur)}</dd></div>
              </dl>
              {mixed && <Note tone="bad">Items with different currencies can&apos;t be combined in one quotation.</Note>}
              <button className="btn btn-primary w-full !py-3" disabled={busy || chosen.length === 0 || mixed} onClick={submit}><Send className="h-5 w-5" aria-hidden /> {busy ? "Sending…" : "Send for Clinic Approval"}</button>
              <p className="text-xs text-muted">An estimate is not final. It becomes a quotation only after a doctor or clinic staff approve it, and charges may change after your examination.</p>
            </section>

            {cfg.inclusions.length > 0 && <section className="card space-y-2"><h3 className="flex items-center gap-2"><Check className="h-5 w-5 text-teal" aria-hidden /> What&apos;s Included</h3>
              <ul className="space-y-1">{cfg.inclusions.map((i) => <li key={i} className="flex items-center gap-2 text-sm"><Check className="h-4 w-4 text-[var(--good-ink)]" aria-hidden />{i}</li>)}</ul></section>}

            <section className="card space-y-2"><h3>Payment Options</h3>
              {cfg.payment_options.length === 0 ? <p className="flex gap-2 text-sm text-muted"><Info className="h-4 w-4 shrink-0" aria-hidden /> Payment options will be confirmed by the clinic when your quotation is approved. No payment is taken in this app.</p>
                : <div role="radiogroup" aria-label="Payment preference" className="space-y-2">{cfg.payment_options.map((o) => (
                  <label key={o} className={`flex items-center gap-3 rounded-2xl border p-3 ${pay === o ? "border-teal bg-teal-soft" : "border-line"}`}><input type="radio" name="pay" className="h-5 w-5 accent-[var(--teal)]" checked={pay === o} onChange={() => setPay(o)} />{o}</label>))}
                  <p className="text-xs text-muted">Your preference is sent with the request. No payment is taken in this app.</p></div>}
            </section>
            {cfg.terms && <p className="text-xs text-muted">{cfg.terms}</p>}
          </aside>
        </div>
      )}

      <section className="space-y-3" aria-label="Your quotations"><h2>Your Quotations</h2>
        {quotes.length === 0 ? <p className="text-muted">You haven&apos;t requested a quotation yet.</p> : quotes.map((q) => (
          <article key={q.id} className="card flex flex-wrap items-center justify-between gap-3">
            <div><p className="font-semibold">Quotation {q.id.slice(0, 8)}</p><p className="text-sm text-muted">{q.lines.map((l) => `${l.name} × ${l.qty}`).join(", ")}</p></div>
            <div className="flex flex-wrap items-center gap-3">{q.status === "approved" ? <><Pill tone="good">Approved by the clinic</Pill><span className="text-lg font-semibold">{money(q.total, q.currency)}</span>
              <button className="btn btn-sm" onClick={() => pdf(q, false)}><Download className="h-4 w-4" aria-hidden /> PDF</button>
              {canShare && <button className="btn btn-sm" onClick={() => pdf(q, true)}><Share2 className="h-4 w-4" aria-hidden /> Share</button>}</>
              : <><Pill tone="warn">Awaiting clinic approval</Pill><span className="text-sm text-muted">Estimate {money(q.total, q.currency)}</span></>}</div>
          </article>))}
      </section>
    </div>
  );
}
