"use client";

import { Plus, UserCog } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Dialog, Note, Pill, useToast } from "@/components/ui";
import { useUser } from "@/components/Shell";
import { api, post } from "@/lib/api";

type U = { id: string; name: string; role: "patient" | "doctor" | "admin"; verified: number; credentials: string; disabled: number };
const ROLES = ["patient", "doctor", "admin"] as const;

/** Account administration (local accounts). Roles are enforced by the server; this screen only calls the audited admin endpoints. */
export default function UserAdmin() {
  const me = useUser();
  const toast = useToast();
  const [users, setUsers] = useState<U[]>([]);
  const [managed, setManaged] = useState(false);
  const [creating, setCreating] = useState(false);
  const [resetFor, setResetFor] = useState<U | null>(null);
  const [form, setForm] = useState({ id: "", name: "", role: "patient", password: "" });
  const [pw, setPw] = useState("");

  const load = useCallback(() => { api<U[]>("/api/admin/users").then(setUsers).catch(() => {}); }, []);
  useEffect(load, [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); toast(ok); load(); }
    catch (e) {
      const msg = e instanceof Error ? e.message : "Failed";
      if (/managed in Supabase/i.test(msg)) setManaged(true);
      toast(msg, "bad");
    }
  }

  return (
    <section className="card space-y-3" aria-label="Users and permissions">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2"><UserCog className="h-6 w-6" aria-hidden /> Users and permissions</h2>
        <button className="btn btn-sm" onClick={() => setCreating(true)}><Plus className="h-4 w-4" aria-hidden /> Add user</button></div>
      <p className="text-sm text-muted">Changing a role removes any recorded clinician verification. Administrators cannot approve clinical content; only a doctor can. Every change is written to the audit log.</p>
      {managed && <Note tone="info">Accounts for this deployment are managed in Supabase Auth.</Note>}
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
        <table><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td><strong>{u.name}</strong><span className="block text-sm text-muted">{u.id}</span></td>
              <td><select className="input !py-1" aria-label={`Role for ${u.id}`} value={u.role} disabled={u.id === me?.id}
                onChange={(e) => run(() => post(`/api/admin/users/${u.id}/role`, { role: e.target.value }), "Role changed.")}>{ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></td>
              <td>{u.disabled ? <Pill tone="bad">disabled</Pill> : <Pill tone="good">active</Pill>} {u.role === "doctor" && (u.verified ? <Pill tone="info">verified</Pill> : <Pill tone="warn">not verified</Pill>)}</td>
              <td className="space-x-2 whitespace-nowrap">
                <button className="btn btn-sm" disabled={u.id === me?.id} onClick={() => run(() => post(`/api/admin/users/${u.id}/disabled`, { disabled: !u.disabled }), u.disabled ? "Account enabled." : "Account disabled.")}>{u.disabled ? "Enable" : "Disable"}</button>
                <button className="btn btn-sm" onClick={() => { setPw(""); setResetFor(u); }}>Reset password</button></td>
            </tr>))}
        </tbody></table></div>

      {creating && (
        <Dialog title="Add user" onClose={() => setCreating(false)}>
          <div className="space-y-3">
            <label className="block text-sm font-semibold">User id<input className="input mt-1 font-normal" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} placeholder="e.g. dr.rao" /></label>
            <label className="block text-sm font-semibold">Name<input className="input mt-1 font-normal" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label className="block text-sm font-semibold">Role<select className="input mt-1 font-normal" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
            <label className="block text-sm font-semibold">Temporary password (at least 12 characters)<input className="input mt-1 font-normal" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
            <button className="btn btn-primary w-full" disabled={!form.id || !form.name || form.password.length < 12}
              onClick={() => run(async () => { await post("/api/admin/users", form); setCreating(false); setForm({ id: "", name: "", role: "patient", password: "" }); }, "User created.")}>Create user</button>
          </div></Dialog>)}

      {resetFor && (
        <Dialog title={`Reset password for ${resetFor.id}`} onClose={() => setResetFor(null)}>
          <div className="space-y-3">
            <label className="block text-sm font-semibold">New password (at least 12 characters)<input className="input mt-1 font-normal" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
            <button className="btn btn-primary w-full" disabled={pw.length < 12} onClick={() => run(async () => { await post(`/api/admin/users/${resetFor.id}/reset-password`, { password: pw }); setResetFor(null); }, "Password reset.")}>Reset password</button>
          </div></Dialog>)}
    </section>
  );
}
