"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Dialog, fmtDateTime } from "@/components/ui";
import { api, post } from "@/lib/api";

type Item = { id: string; kind: string; title: string; body: string; link: string; created_at: number; read_at: number | null };
type Inbox = { unread: number; items: Item[] };

/** In-app notifications only (no email/SMS/push exists). Polls while the tab is visible; the count is announced politely to assistive technology. */
export default function NotificationBell() {
  const [box, setBox] = useState<Inbox>({ unread: 0, items: [] });
  const [open, setOpen] = useState(false);
  const load = useCallback(() => { api<Inbox>("/api/notifications").then(setBox).catch(() => {}); }, []);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, 30000);
    return () => clearInterval(t);
  }, [load]);

  async function markAll() { await post("/api/notifications/read-all"); load(); }
  async function visit(n: Item) { if (!n.read_at) await post(`/api/notifications/${n.id}/read`).catch(() => {}); setOpen(false); load(); }

  return (
    <>
      <button className="relative grid h-11 w-11 shrink-0 place-items-center rounded-full border border-line bg-white text-teal-dark" onClick={() => { setOpen(true); load(); }}
        aria-label={box.unread ? `Notifications, ${box.unread} unread` : "Notifications, none unread"}>
        <Bell className="h-5 w-5" aria-hidden />
        {box.unread > 0 && <span className="absolute -right-1 -top-1 grid min-h-5 min-w-5 place-items-center rounded-full bg-[var(--bad-ink)] px-1 text-xs font-bold text-white" aria-hidden>{box.unread > 9 ? "9+" : box.unread}</span>}
      </button>
      <span className="sr-only" role="status" aria-live="polite">{box.unread ? `${box.unread} unread notifications` : ""}</span>
      {open && (
        <Dialog title="Notifications" onClose={() => setOpen(false)}>
          {box.items.length === 0 ? <p className="text-muted">Nothing new. You will see updates here when a report is ready or a clinician reviews something.</p> : (
            <ul className="space-y-2">
              {box.items.map((n) => (
                <li key={n.id}>
                  <Link href={n.link || "/"} onClick={() => visit(n)} className={`block rounded-2xl border p-3 ${n.read_at ? "border-line bg-white" : "border-teal bg-teal-soft"}`}>
                    <span className="block font-semibold">{n.title}{!n.read_at && <span className="sr-only"> (unread)</span>}</span>
                    {n.body && <span className={`block text-sm ${n.read_at ? "text-muted" : "text-teal-dark"}`}>{n.body}</span>}
                    <span className={`block text-xs ${n.read_at ? "text-muted" : "text-teal-dark"}`}>{fmtDateTime(n.created_at)}</span>
                  </Link>
                </li>))}
            </ul>)}
          {box.unread > 0 && <button className="btn mt-3 w-full" onClick={markAll}>Mark all as read</button>}
        </Dialog>)}
    </>
  );
}
