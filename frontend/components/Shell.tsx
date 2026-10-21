"use client";

import ConsentGate, { ConsentStatus } from "@/components/ConsentGate";
import NotificationBell from "@/components/NotificationBell";
import { ClipboardList, Camera, History, Home, LogOut, MessageCircle, FileText, ChevronLeft, Settings, ShieldCheck, WifiOff } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api, getRawSession, signOutEverywhere, subscribeSession, User } from "@/lib/api";
import { isPreview } from "@/lib/preview";
import { checkServer, getApiBase, serverLabel } from "@/lib/config";
import { connection } from "@/lib/connection";
import NativeInit from "@/components/NativeInit";
import { PhotoFlowProvider } from "@/lib/photoFlow";
import { BrandLogo, Dialog, ToastProvider } from "@/components/ui";

// These two are local constants ON PURPOSE. process.env.NEXT_PUBLIC_* is replaced by a literal at build time, so the bundler folds them and, in a release build, removes everything behind them
// (the server dialog, preview mode, demo accounts). A flag imported from another module would not be folded. lib/config.ts defines the same flags for code that does not need to disappear.
const IS_RELEASE = process.env.NEXT_PUBLIC_TARGET === "android" && process.env.NEXT_PUBLIC_RELEASE === "1";
const DEV_TOOLS = process.env.NEXT_PUBLIC_TARGET === "android" && process.env.NEXT_PUBLIC_RELEASE !== "1";

const Ctx = createContext<User | null>(null);
export const useUser = () => useContext(Ctx);

const subscribeOnline = (cb: () => void) => { window.addEventListener("online", cb); window.addEventListener("offline", cb); return () => { window.removeEventListener("online", cb); window.removeEventListener("offline", cb); }; };
/** The status banners at the top of every screen, stacked in one sticky block so they never overlap:
 *  - no connection at all: nothing can be sent or loaded until it is back;
 *  - online but the server did not answer: say so and offer a retry (a successful retry reloads the current screen's data); the sign-in screens show this state themselves;
 *  - preview mode: always labelled, with a way out. */
function TopBanners({ showServer, preview, onRetried, onExitPreview }: { showServer: boolean; preview: boolean; onRetried: () => void; onExitPreview: () => void }) {
  const down = useSyncExternalStore(connection.subscribe, connection.isDown, () => false);
  const online = useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true);
  const [busy, setBusy] = useState(false);
  const retry = async () => {
    setBusy(true);
    const r = await checkServer(getApiBase());
    setBusy(false);
    if (r.ok) { connection.markUp(); onRetried(); }
  };
  const row = "flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 py-2 text-center text-sm font-semibold";
  const items = [
    !online && <div key="offline" role="status" className={`${row} bg-[var(--warn-bg)] text-[var(--warn-ink)]`}><span className="inline-flex items-center gap-2"><WifiOff className="h-4 w-4 shrink-0" aria-hidden /> You are offline. Nothing can be sent or loaded until you reconnect.</span></div>,
    online && showServer && down && <div key="server" role="status" className={`${row} bg-[var(--bad-bg)] text-[var(--bad-ink)]`}>
      <span className="inline-flex items-center gap-2"><WifiOff className="h-4 w-4 shrink-0" aria-hidden /> {IS_RELEASE ? "Can't reach Cosmo Experts." : `Can't reach the server (${serverLabel()}).`}</span>
      <button type="button" className="underline" onClick={retry} disabled={busy}>{busy ? "Checking…" : "Try again"}</button></div>,
    DEV_TOOLS && preview && <div key="preview" role="status" className={`${row} bg-gold-soft text-[#86622a]`}><span>PREVIEW · no server is connected · nothing is saved, sent or analysed</span>
      <button type="button" className="underline" onClick={onExitPreview}>Connect to a server</button></div>,
  ].filter(Boolean);
  if (!items.length) return null;
  return <div className="sticky top-0 z-50 bg-ivory shadow-sm" style={{ paddingTop: "env(safe-area-inset-top)" }}>{items}</div>;
}

const PATIENT_NAV = [
  { href: "/", label: "Home", Icon: Home },
  { href: "/reports", label: "Reports", Icon: FileText },
  { href: "/photo", label: "Photo", Icon: Camera, fab: true },
  { href: "/ask", label: "Ask AI", Icon: MessageCircle },
  { href: "/history", label: "History", Icon: History },
];
const STAFF_NAV: Record<string, { href: string; label: string }[]> = {
  doctor: [{ href: "/clinic", label: "Clinic workspace" }],
  admin: [{ href: "/clinic", label: "Clinic workspace" }, { href: "/admin", label: "Admin & AI lab" }],
};

const PUBLIC = ["/login", "/register", "/forgot-password", "/reset-password"];
const isActive = (path: string, href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));

/** Back + brand + optional right slot, used at the top of every sub-screen on mobile. */
export function ScreenBar({ back = true, right }: { back?: boolean | string; right?: React.ReactNode }) {
  const router = useRouter();
  return (
    <div className="mb-5 grid grid-cols-[auto_minmax(0,1fr)_minmax(0,auto)] items-center gap-2 md:hidden">
      {back ? (
        <button className="btn btn-ghost justify-self-start !px-1 !text-base text-teal-dark" onClick={() => (typeof back === "string" ? router.push(back) : router.back())} aria-label="Go back">
          <ChevronLeft className="h-6 w-6" aria-hidden /> Back
        </button>
      ) : <span />}
      <span className="min-w-0 justify-self-center text-center"><BrandLogo small /></span>
      <div className="min-w-[2.5rem] justify-self-end">{right}</div>
    </div>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const raw = useSyncExternalStore(subscribeSession, getRawSession, () => null);
  const ready = useSyncExternalStore(() => () => {}, () => true, () => false);
  const user = useMemo<User | null>(() => { try { return raw ? (JSON.parse(raw).user as User) : null; } catch { return null; } }, [raw]);
  const rawPath = usePathname() ?? "/";
  const path = rawPath.length > 1 ? rawPath.replace(/\/+$/, "") : rawPath;       // the static Android export uses trailing slashes (/login/); every rule below is written without them
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [reload, setReload] = useState(0);                 // bumping this remounts the current screen so it loads its data again after a successful retry
  const [consentFor, setConsentFor] = useState<{ uid: string; status: ConsentStatus } | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!user && !PUBLIC.includes(path)) router.replace("/login");
    if (user && user.role !== "patient" && !path.startsWith("/clinic") && !path.startsWith("/admin") && path !== "/login") router.replace("/clinic");
    if (user && user.role === "patient" && (path.startsWith("/clinic") || path.startsWith("/admin"))) router.replace("/");     // staff pages are never shown to patients (the API refuses them too)
  }, [ready, user, path, router]);

  const uid = user?.id, urole = user?.role;
  useEffect(() => {
    if (!uid || urole !== "patient") return;
    api<ConsentStatus>("/api/consent").then((status) => setConsentFor({ uid, status })).catch(() => {});
  }, [uid, urole]);
  const consent = urole === "patient" && consentFor && consentFor.uid === uid ? consentFor.status : null;       // never reuse another user's answer

  const signOut = () => { setMenu(false); void signOutEverywhere(); router.replace("/login"); };

  if (!ready) return null;
  const blocked = !user ? !PUBLIC.includes(path) : user.role === "patient" ? path.startsWith("/clinic") || path.startsWith("/admin") : !path.startsWith("/clinic") && !path.startsWith("/admin") && path !== "/login";
  const patient = user?.role === "patient";
  const initials = (user?.name ?? "").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <Ctx.Provider value={user}>
      <ToastProvider>
        <PhotoFlowProvider>
          <TopBanners showServer={!PUBLIC.includes(path)} preview={DEV_TOOLS && !!user && isPreview()} onRetried={() => setReload((n) => n + 1)} onExitPreview={() => { void signOutEverywhere(); router.replace("/login"); }} />
          <NativeInit />
          {user && (
            <header className={`sticky top-0 z-30 border-b border-line bg-white/85 pt-[env(safe-area-inset-top)] backdrop-blur ${patient && path !== "/" ? "hidden md:block" : ""}`}>
              <div className="mx-auto flex max-w-[1040px] items-center gap-3 px-[18px] py-2.5 md:gap-6 md:px-7">
                <Link href={patient ? "/" : "/clinic"} aria-label="Cosmo Experts home" className="min-w-0"><BrandLogo compact={!patient} /></Link>
                <nav aria-label="Main" className="hidden flex-1 items-center gap-1 md:flex">
                  {(patient ? PATIENT_NAV : STAFF_NAV[user.role]).map((n) => (
                    <Link key={n.href} href={n.href} aria-current={isActive(path, n.href) ? "page" : undefined}
                      className={`flex items-center rounded-full px-4 py-2 font-semibold ${isActive(path, n.href) ? "bg-teal-soft text-teal-dark" : "text-muted hover:text-teal-dark"}`}>{n.label}</Link>
                  ))}
                </nav>
                <span className="flex-1 md:hidden" />
                <NotificationBell />
                <button className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-teal text-sm font-bold text-white" onClick={() => setMenu(true)} aria-label={`Account menu for ${user.name}`}>{initials}</button>
              </div>
            </header>
          )}
          <main id="main" key={reload} className={`page ${!user || (patient && path !== "/") ? "page-top" : ""}`}>{blocked ? null : consent?.required ? <ConsentGate status={consent} onDone={(status) => uid && setConsentFor({ uid, status })} /> : children}</main>
          {patient && (
            <nav aria-label="Primary" className="bottom-nav grid grid-cols-[repeat(5,minmax(0,1fr))] items-center md:hidden">
              {PATIENT_NAV.map(({ href, label, Icon, fab }) => (
                <Link key={href} href={href} aria-current={isActive(path, href) ? "page" : undefined} aria-label={label}>
                  {fab ? <span className="fab"><Icon className="h-7 w-7" aria-hidden /></span> : <><Icon className="h-6 w-6" aria-hidden />{label}</>}
                </Link>
              ))}
            </nav>
          )}
          {menu && user && (
            <Dialog title="Your account" onClose={() => setMenu(false)}>
              <p className="mb-1 font-semibold">{user.name}</p>
              <p className="mb-5 text-sm capitalize text-muted">{user.role}</p>
              <Link href="/settings" className="btn mb-3 w-full" onClick={() => setMenu(false)}><Settings className="h-5 w-5" aria-hidden /> Settings</Link>
              {patient && <Link href="/privacy" className="btn mb-3 w-full" onClick={() => setMenu(false)}><ShieldCheck className="h-5 w-5" aria-hidden /> Privacy and consent</Link>}
              {patient && <Link href="/care" className="btn mb-3 w-full" onClick={() => setMenu(false)}><ClipboardList className="h-5 w-5" aria-hidden /> Doctor review &amp; care plan</Link>}
              <button className="btn btn-primary w-full" onClick={signOut}><LogOut className="h-5 w-5" aria-hidden /> Sign out</button>
            </Dialog>
          )}
        </PhotoFlowProvider>
      </ToastProvider>
    </Ctx.Provider>
  );
}
