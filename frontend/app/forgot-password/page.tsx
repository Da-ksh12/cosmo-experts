"use client";

import Link from "next/link";
import { useState } from "react";
import { BrandLogo, Note } from "@/components/ui";
import { post } from "@/lib/api";
import { IS_APP, WEB_ORIGIN } from "@/lib/config";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // The link in the e-mail opens in a browser, which cannot open the app's own internal address: the app sends people to the clinic's public web page (WEB_ORIGIN) or does not offer a reset at all.
  const unavailable = IS_APP && !WEB_ORIGIN;
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setBusy(true);
    try { setDone((await post<{ message: string }>("/api/auth/password-reset/request", { email, redirect_to: `${IS_APP ? WEB_ORIGIN : window.location.origin}/reset-password` })).message); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not send the message."); }
    setBusy(false);
  }
  return (
    <div className="mx-auto max-w-md pt-6">
      <div className="mb-8"><BrandLogo /></div>
      <div className="card space-y-4">
        <h1 className="!text-3xl">Reset your password</h1>
        {unavailable ? <Note tone="info">To reset your password, please contact your clinic.</Note> : done ? <Note tone="ok">{done}</Note> : (
          <form onSubmit={go} className="space-y-4">
            <label className="block font-semibold">Email<input className="input mt-1 font-normal" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required /></label>
            {err && <Note tone="bad">{err}</Note>}
            <button className="btn btn-primary w-full !py-3" type="submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
          </form>)}
        <p className="text-center text-sm"><Link href="/login" className="font-semibold text-teal-dark underline">Back to sign in</Link></p>
      </div>
    </div>
  );
}
