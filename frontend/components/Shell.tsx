"use client";

import ConsentGate, { ConsentStatus } from "@/components/ConsentGate";
import NotificationBell from "@/components/NotificationBell";
import { ClipboardList, Camera, History, Home, LogOut, MessageCircle, FileText, ChevronLeft, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api, getRawSession, signOutEverywhere, subscribeSession, User } from "@/lib/api";
import { PhotoFlowProvider } from "@/lib/photoFlow";
import { BrandLogo, Dialog, ToastProvider } from "@/components/ui";

const Ctx = createContext<User | null>(null);
export const useUser = () => useContext(Ctx);

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
  const path = usePathname();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
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
          {user && (
            <header className={`sticky top-0 z-30 border-b border-line bg-white/85 backdrop-blur ${patient && path !== "/" ? "hidden md:block" : ""}`}>
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
          <main id="main" className="page">{blocked ? null : consent?.required ? <ConsentGate status={consent} onDone={(status) => uid && setConsentFor({ uid, status })} /> : children}</main>
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
