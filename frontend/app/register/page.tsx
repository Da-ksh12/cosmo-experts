"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BrandLogo, Note } from "@/components/ui";
import { api, post } from "@/lib/api";

export default function Register() {
  const [open, setOpen] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<{ registration: boolean }>("/api/auth/config").then((c) => setOpen(c.registration)).catch(() => setOpen(false)); }, []);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try { setDone((await post<{ message: string }>("/api/auth/register", { email, password: pw, full_name: name })).message); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not create the account."); }
    setBusy(false);
  }
  return (
    <div className="mx-auto max-w-md pt-6">
      <div className="mb-8"><BrandLogo /></div>
      <div className="card space-y-4">
        <h1 className="!text-3xl">Create your account</h1>
        {open === false ? <Note tone="info">Online registration is not open. Please contact the clinic to have an account created for you.</Note>
          : done ? <Note tone="ok">{done}</Note> : (
            <form onSubmit={go} className="space-y-4">
              <label className="block font-semibold">Your name<input className="input mt-1 font-normal" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={80} required /></label>
              <label className="block font-semibold">Email<input className="input mt-1 font-normal" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required /></label>
              <label className="block font-semibold">Password<input className="input mt-1 font-normal" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" minLength={12} maxLength={128} required />
                <span className="mt-1 block text-sm font-normal text-muted">At least 12 characters. A short sentence works well.</span></label>
              {err && <Note tone="bad">{err}</Note>}
              <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy || open === null}>{busy ? "Creating…" : "Create account"}</button>
            </form>)}
        <p className="text-center text-sm"><Link href="/login" className="font-semibold text-teal-dark underline">Back to sign in</Link></p>
      </div>
    </div>
  );
}
