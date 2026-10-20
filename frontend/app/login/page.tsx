"use client";

import { Eye, LogIn, Server, WifiOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ServerSettings from "@/components/ServerSettings";
import { BrandLogo, Leaf, Note } from "@/components/ui";
import { ApiError, api, getSession, post, refreshSession, setSession, User } from "@/lib/api";
import { getApiBase, IS_APP, onApiBaseChange, serverLabel } from "@/lib/config";
import { setPreview } from "@/lib/preview";

const DEMO = [
  { id: "patient1", label: "Patient (Demo One)" },
  { id: "patient2", label: "Patient (Demo Two)" },
  { id: "doctor1", label: "Doctor" },
  { id: "admin1", label: "Administrator" },
];

const OFFERED = IS_APP ? DEMO.filter((d) => d.id !== "admin1") : DEMO;      // the phone app is for patients and clinicians; the administrator console is a desktop screen

type AuthConfig = { mode: "local" | "supabase"; registration: boolean; password_min: number };

export default function Login() {
  const router = useRouter();
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [userId, setUserId] = useState("patient1");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState("");                // why the server could not be reached (never hidden, never replaced by a pretend login form)
  const [showServer, setShowServer] = useState(false);
  const [rev, setRev] = useState(0);
  useEffect(() => onApiBaseChange(() => setRev((n) => n + 1)), []);
  const needsServer = IS_APP && !getApiBase();
  const startPreview = () => { setPreview(true); setSession({ token: "preview", user: { id: "preview", name: "Preview visitor", role: "patient" } }); router.replace("/"); };

  useEffect(() => {
    if (IS_APP && !getApiBase()) return;                     // no server chosen yet: nothing to ask
    let live = true;
    const home = (u: User) => router.replace(u.role === "patient" ? "/" : "/clinic");
    api<AuthConfig>("/api/auth/config").then(async (c) => {
      if (!live) return;
      setOffline("");
      setCfg(c);
      if (c.mode === "supabase" && (await refreshSession())) {            // a returning visitor with a valid refresh cookie is signed in without typing a password
        const s = getSession();
        if (s && live) home(s.user);
      }
    }).catch((e) => {
      if (!live) return;
      setOffline(e instanceof Error ? e.message : "We can't reach the server right now.");
      setCfg({ mode: "local", registration: false, password_min: 12 });
    });
    return () => { live = false; };
  }, [router, rev]);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await post<{ token: string; user: User }>("/api/auth/login", { user_id: cfg?.mode === "supabase" ? email : userId, password: pw });
      setSession({ token: r.token, user: r.user });
      router.replace(r.user.role === "patient" ? "/" : "/clinic");
    } catch (e) {
      const code = e instanceof ApiError && e.detail && typeof e.detail === "object" ? (e.detail as { code?: string }).code : undefined;
      setErr(code === "email_not_confirmed" ? "Please confirm your email address using the link we sent you, then sign in." : e instanceof Error ? e.message : "Could not sign in.");
      setBusy(false);
    }
  }
  const sb = cfg?.mode === "supabase";

  return (
    <div className="relative mx-auto max-w-md pt-6">
      <Leaf className="pointer-events-none absolute -right-8 -top-2 h-40 w-40" />
      <div className="mb-8 rise"><BrandLogo /></div>
      {needsServer ? (
        <div className="card rise-2 space-y-4">
          <h1 className="!text-3xl">Connect to your clinic</h1>
          <p className="text-muted">This app needs the address of your Cosmo Experts server before you can sign in. Your clinic or IT team can give it to you.</p>
          <button className="btn btn-primary w-full !py-3" type="button" onClick={() => setShowServer(true)}><Server className="h-5 w-5" aria-hidden /> Set the server address</button>
          <button className="btn w-full !py-3" type="button" onClick={startPreview}><Eye className="h-5 w-5" aria-hidden /> Look around in preview mode</button>
          <p className="text-center text-xs text-muted">Preview shows the screens only. Nothing is saved, sent or analysed, and no answers or results are produced.</p>
        </div>
      ) : (
      <form onSubmit={go} className="card rise-2 space-y-4">
        <h1 className="!text-3xl">Welcome back</h1>
        {offline && (
          <Note tone="bad">
            <span className="flex items-start gap-2"><WifiOff className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> <span>{offline}</span></span>
            <span className="mt-2 flex flex-wrap gap-3">
              <button type="button" className="font-semibold underline" onClick={() => setRev((n) => n + 1)}>Try again</button>
              {IS_APP && <button type="button" className="font-semibold underline" onClick={() => setShowServer(true)}>Change server</button>}
              {IS_APP && <button type="button" className="font-semibold underline" onClick={startPreview}>Preview mode</button>}
            </span>
          </Note>)}
        {cfg && !sb && !offline && <Note tone="info">Local development mode: demo accounts only. Production uses Supabase Auth.</Note>}
        {sb ? (
          <label className="block font-semibold">Email
            <input className="input mt-1 font-normal" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required /></label>
        ) : (
          <label className="block font-semibold">Account
            <select className="input mt-1 font-normal" value={userId} onChange={(e) => setUserId(e.target.value)}>{OFFERED.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
        )}
        <label className="block font-semibold">Password
          <input className="input mt-1 font-normal" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        {err && <Note tone="bad">{err}</Note>}
        <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy || !cfg || !!offline}><LogIn className="h-5 w-5" aria-hidden /> {busy ? "Signing in…" : "Sign in"}</button>
        {sb && (
          <p className="text-center text-sm">
            <Link href="/forgot-password" className="font-semibold text-teal-dark underline">Forgot your password?</Link>
            {cfg?.registration && <> · <Link href="/register" className="font-semibold text-teal-dark underline">Create an account</Link></>}
          </p>)}
        {IS_APP && <p className="text-center text-sm text-muted">Server: {serverLabel()} · <button type="button" className="font-semibold text-teal-dark underline" onClick={() => setShowServer(true)}>Change</button></p>}
      </form>)}
      {showServer && <ServerSettings onClose={() => setShowServer(false)} onSaved={() => setRev((n) => n + 1)} />}
    </div>
  );
}
