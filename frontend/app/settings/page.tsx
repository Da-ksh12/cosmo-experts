"use client";

import { ChevronRight, ClipboardList, LogOut, Server, ShieldCheck } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ScreenBar, useUser } from "@/components/Shell";
import { Note, PageHeading, Pill } from "@/components/ui";
import { signOutEverywhere } from "@/lib/api";
import { isPreview } from "@/lib/preview";
import { APP_VERSION, checkServer, getApiBase, IS_APP, onApiBaseChange, serverLabel } from "@/lib/config";

// A local constant on purpose: folded by the bundler, so a release build contains none of the developer tools behind it (see app/login/page.tsx).
const DEV_TOOLS = process.env.NEXT_PUBLIC_TARGET === "android" && process.env.NEXT_PUBLIC_RELEASE !== "1";

const ServerSettings = DEV_TOOLS ? dynamic(() => import("@/components/ServerSettings")) : null;      // developer builds only

type Conn = { state: "checking" | "ok" | "bad"; message: string };

export default function Settings() {
  const user = useUser();
  const router = useRouter();
  const [showServer, setShowServer] = useState(false);
  const [rev, setRev] = useState(0);
  const [link, setLink] = useState<Conn>({ state: "checking", message: "Checking…" });
  useEffect(() => onApiBaseChange(() => setRev((n) => n + 1)), []);

  useEffect(() => {
    if (!DEV_TOOLS || isPreview()) return;
    let live = true;
    checkServer(getApiBase()).then((r) => { if (live) setLink({ state: r.ok ? "ok" : "bad", message: r.message }); });
    return () => { live = false; };
  }, [rev]);

  const initials = (user?.name ?? "").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  const row = "card flex w-full items-center gap-3 !p-4 text-left no-underline";

  return (
    <div className="space-y-5">
      <ScreenBar back="/" />
      <PageHeading eyebrow="Your account" title="Settings" />

      <section className="card flex items-center gap-4" aria-label="Profile">
        <span className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-teal text-lg font-bold text-white" aria-hidden>{initials || "?"}</span>
        <div className="min-w-0 flex-1"><p className="truncate text-lg font-semibold">{user?.name ?? "Signed out"}</p><p className="text-sm capitalize text-muted">{user?.role}</p></div>
      </section>

      {DEV_TOOLS && (
        <section className="card space-y-3" aria-label="Server connection">
          <h2 className="flex items-center gap-2 !text-lg"><Server className="h-5 w-5 text-teal" aria-hidden /> Server connection <Pill tone="warn">Developer build</Pill></h2>
          <p className="break-all font-semibold">{serverLabel()}</p>
          <div role="status" aria-live="polite">
            {isPreview() && <Pill tone="warn">Preview mode: no server connected</Pill>}
            {!isPreview() && link.state === "checking" && <Pill tone="info">Checking…</Pill>}
            {!isPreview() && link.state === "ok" && <Pill tone="good">Connected</Pill>}
            {!isPreview() && link.state === "bad" && <Note tone="bad">{link.message}</Note>}
          </div>
          <div className="flex flex-wrap gap-3">
            <button className="btn" onClick={() => { setLink({ state: "checking", message: "Checking…" }); setRev((n) => n + 1); }}>Test again</button>
            <button className="btn btn-primary" onClick={() => setShowServer(true)}>Change server</button>
          </div>
        </section>)}

      {user?.role === "patient" && (
        <nav className="space-y-3" aria-label="Privacy and care">
          <Link href="/privacy" className={row}><ShieldCheck className="h-6 w-6 shrink-0 text-teal" aria-hidden /><span className="min-w-0 flex-1"><span className="block font-semibold">Privacy and consent</span><span className="block text-sm text-muted">What you agreed to, and how to change it</span></span><ChevronRight className="h-5 w-5 text-muted" aria-hidden /></Link>
          <Link href="/care" className={row}><ClipboardList className="h-6 w-6 shrink-0 text-teal" aria-hidden /><span className="min-w-0 flex-1"><span className="block font-semibold">Doctor review and care plan</span><span className="block text-sm text-muted">What your clinician has reviewed</span></span><ChevronRight className="h-5 w-5 text-muted" aria-hidden /></Link>
        </nav>)}

      <section className="space-y-1 px-1" aria-label="About">
        <p className="text-sm text-muted">Cosmo Experts AI explains your photos and reports in plain language, with clinician review. It does not diagnose or prescribe, and it has not been clinically validated.</p>
        <p className="text-xs text-muted">Version {APP_VERSION}{IS_APP ? "" : " · web"}</p>
      </section>

      <button className="btn btn-primary w-full !py-3" onClick={() => { void signOutEverywhere(); router.replace("/login"); }}><LogOut className="h-5 w-5" aria-hidden /> Sign out</button>
      {showServer && ServerSettings && <ServerSettings onClose={() => setShowServer(false)} onSaved={() => setRev((n) => n + 1)} />}
    </div>
  );
}
