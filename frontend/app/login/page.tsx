"use client";

import { LogIn } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BrandLogo, Leaf, Note } from "@/components/ui";
import { ApiError, api, getSession, post, refreshSession, setSession, User } from "@/lib/api";

const DEMO = [
  { id: "patient1", label: "Patient (Demo One)" },
  { id: "patient2", label: "Patient (Demo Two)" },
  { id: "doctor1", label: "Doctor" },
  { id: "admin1", label: "Administrator" },
];

type AuthConfig = { mode: "local" | "supabase"; registration: boolean; password_min: number };

export default function Login() {
  const router = useRouter();
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [userId, setUserId] = useState("patient1");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const home = (u: User) => router.replace(u.role === "patient" ? "/" : "/clinic");
    api<AuthConfig>("/api/auth/config").then(async (c) => {
      if (!live) return;
      setCfg(c);
      if (c.mode === "supabase" && (await refreshSession())) {            // a returning visitor with a valid refresh cookie is signed in without typing a password
        const s = getSession();
        if (s && live) home(s.user);
      }
    }).catch(() => { if (live) setCfg({ mode: "local", registration: false, password_min: 12 }); });
    return () => { live = false; };
  }, [router]);

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
      <form onSubmit={go} className="card rise-2 space-y-4">
        <h1 className="!text-3xl">Welcome back</h1>
        {cfg && !sb && <Note tone="info">Local development mode: demo accounts only. Production uses Supabase Auth.</Note>}
        {sb ? (
          <label className="block font-semibold">Email
            <input className="input mt-1 font-normal" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required /></label>
        ) : (
          <label className="block font-semibold">Account
            <select className="input mt-1 font-normal" value={userId} onChange={(e) => setUserId(e.target.value)}>{DEMO.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
        )}
        <label className="block font-semibold">Password
          <input className="input mt-1 font-normal" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        {err && <Note tone="bad">{err}</Note>}
        <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy || !cfg}><LogIn className="h-5 w-5" aria-hidden /> {busy ? "Signing in…" : "Sign in"}</button>
        {sb && (
          <p className="text-center text-sm">
            <Link href="/forgot-password" className="font-semibold text-teal-dark underline">Forgot your password?</Link>
            {cfg?.registration && <> · <Link href="/register" className="font-semibold text-teal-dark underline">Create an account</Link></>}
          </p>)}
      </form>
    </div>
  );
}
