"use client";

import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useState } from "react";
import { Dialog, Note } from "@/components/ui";
import { checkServer, defaultApiBase, getApiBase, parseServerUrl, setApiBase } from "@/lib/config";

type Check = { kind: "idle" | "testing" | "ok" | "bad"; message?: string };

/** Where this app finds its Cosmo Experts server. The address is saved on this phone only. Nothing secret is entered or stored here: sign-in happens on the login screen. */
export default function ServerSettings({ onClose, onSaved }: { onClose: () => void; onSaved?: () => void }) {
  const [text, setText] = useState(getApiBase());
  const [check, setCheck] = useState<Check>({ kind: "idle" });
  const parsed = parseServerUrl(text);
  const fallback = defaultApiBase();

  async function test() {
    if (!parsed.ok) { setCheck({ kind: "bad", message: parsed.error }); return; }
    setCheck({ kind: "testing" });
    const r = await checkServer(parsed.base);
    setCheck({ kind: r.ok ? "ok" : "bad", message: r.message });
  }
  function save() {
    if (!parsed.ok) { setCheck({ kind: "bad", message: parsed.error }); return; }
    setApiBase(parsed.base);
    onSaved?.();
    onClose();
  }

  return (
    <Dialog title="Server connection" onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-muted">Cosmo Experts AI works with a secure server run by your clinic or your IT team. Enter its address, for example <span className="font-semibold">https://api.example.com</span>.</p>
        <label className="block font-semibold" htmlFor="server-url">Server address
          <input id="server-url" className="input mt-1 font-normal" type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off"
            placeholder="https://" value={text} onChange={(e) => { setText(e.target.value); setCheck({ kind: "idle" }); }} />
        </label>
        <div role="status" aria-live="polite" className="min-h-[1.5rem] text-sm">
          {check.kind === "testing" && <span className="inline-flex items-center gap-2 text-muted"><Loader2 className="spin h-4 w-4" aria-hidden /> Checking the server…</span>}
          {check.kind === "ok" && <span className="inline-flex items-center gap-2 font-semibold text-[var(--good-ink)]"><CheckCircle2 className="h-4 w-4" aria-hidden /> {check.message}</span>}
          {check.kind === "bad" && <span className="inline-flex items-start gap-2 text-[var(--bad-ink)]"><XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> <span>{check.message}</span></span>}
        </div>
        <Note tone="info">Use an <strong>https://</strong> address. A plain <strong>http://</strong> address works only for a computer on your own network that was allowed when this app was built.</Note>
        <div className="flex flex-wrap gap-3">
          <button className="btn" type="button" onClick={test} disabled={check.kind === "testing"}>Test connection</button>
          <button className="btn btn-primary" type="button" onClick={save} disabled={!text.trim()}>Save</button>
          {fallback && fallback !== text && <button className="btn btn-ghost" type="button" onClick={() => { setText(fallback); setCheck({ kind: "idle" }); }}>Use the built-in address</button>}
        </div>
      </div>
    </Dialog>
  );
}
