"use client";

import { LogIn } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { BrandLogo, Leaf, Note } from "@/components/ui";
import { post, setSession, User } from "@/lib/api";

const DEMO = [
  { id: "patient1", label: "Patient (Demo One)" },
  { id: "patient2", label: "Patient (Demo Two)" },
  { id: "doctor1", label: "Doctor" },
  { id: "admin1", label: "Administrator" },
];

export default function Login() {
  const router = useRouter();
  const [userId, setUserId] = useState("patient1");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const r = await post<{ token: string; user: User }>("/api/auth/login", { user_id: userId, password: pw });
      setSession(r);
      router.replace(r.user.role === "patient" ? "/" : "/clinic");
    } catch (e) { setErr(e instanceof Error ? e.message : "Could not sign in."); setBusy(false); }
  }

  return (
    <div className="relative mx-auto max-w-md pt-6">
      <Leaf className="pointer-events-none absolute -right-8 -top-2 h-40 w-40" />
      <div className="mb-8 rise"><BrandLogo /></div>
      <form onSubmit={go} className="card rise-2 space-y-4">
        <h1 className="!text-3xl">Welcome back</h1>
        <Note tone="info">Local development mode: demo accounts only. Production uses Supabase Auth.</Note>
        <label className="block font-semibold">Account
          <select className="input mt-1 font-normal" value={userId} onChange={(e) => setUserId(e.target.value)}>{DEMO.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
        <label className="block font-semibold">Password
          <input className="input mt-1 font-normal" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" required /></label>
        {err && <Note tone="bad">{err}</Note>}
        <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy}><LogIn className="h-5 w-5" aria-hidden /> {busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
