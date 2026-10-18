"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BrandLogo, Note } from "@/components/ui";
import { post } from "@/lib/api";

/** The recovery link puts a short-lived token in the URL fragment (browsers never send the fragment to a server). It is read once, removed from the address bar, and kept only in memory. */
export default function Reset() {
  const [token, setToken] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const p = new URLSearchParams(window.location.hash.slice(1));
    const t = p.get("type") === "recovery" ? p.get("access_token") : null;
    window.history.replaceState(null, "", window.location.pathname);
    if (t) queueMicrotask(() => setToken(t));
  }, []);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try { await post("/api/auth/password-reset/confirm", { recovery_token: token, new_password: pw }); setDone(true); setToken(""); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not reset the password."); }
    setBusy(false);
  }
  return (
    <div className="mx-auto max-w-md pt-6">
      <div className="mb-8"><BrandLogo /></div>
      <div className="card space-y-4">
        <h1 className="!text-3xl">Choose a new password</h1>
        {done ? <Note tone="ok">Your password has been changed. You have been signed out everywhere; please sign in with the new password.</Note>
          : !token ? <Note tone="warn">This page needs the link from your reset email. <Link href="/forgot-password" className="underline">Request a new link</Link>.</Note> : (
            <form onSubmit={go} className="space-y-4">
              <label className="block font-semibold">New password<input className="input mt-1 font-normal" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" minLength={12} maxLength={128} required />
                <span className="mt-1 block text-sm font-normal text-muted">At least 12 characters.</span></label>
              {err && <Note tone="bad">{err}</Note>}
              <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
            </form>)}
        <p className="text-center text-sm"><Link href="/login" className="font-semibold text-teal-dark underline">Back to sign in</Link></p>
      </div>
    </div>
  );
}
