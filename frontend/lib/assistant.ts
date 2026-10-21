"use client";

import { api } from "@/lib/api";

/** The assistant's answer card (assistant-card/1), mirrored from backend/app/assistant/compose.py (documented in docs/ASSISTANT_ARCHITECTURE.md).
 *  A card is plain data the server built from the dermatology library, the person's own records or the clinic's own configuration. The app only draws it: nothing here is generated on the phone,
 *  and a card that is missing a part simply does not show that part. */
export type ActionKind = "message" | "navigate" | "external" | "confirm" | "report";
export type CardAction = {
  id: string; label: string; kind: ActionKind;
  text?: string; route?: string; href?: string; document_id?: string; proposal_id?: string; tool?: string;
};
export type SectionTone = "info" | "care" | "avoid" | "urgent";
export type CardSection = { id: string; title: string; items: string[]; tone?: SectionTone };
export type Choice = { label: string; reply: string };
export type FollowUp = { id: string; text: string; choices: Choice[] };
export type Escalation = { level: "emergency" | "urgent" | "prompt"; title: string; reason: string; message: string; rules?: string[]; sources?: string[]; actions?: CardAction[] };
export type CardSourceRef = { id: string; publisher: string; title: string; url: string; page_reviewed?: string | null; next_review_due?: string | null; source_review_overdue?: boolean; terms?: string };
export type Review = { tier: "approved" | "reference" | "record" | "clinic" | "clinician" | string; label: string; articles?: { id: string; title: string; version?: number; reviewed_at?: string | null }[] };
export type ReportItem = { field_id: string; name: string; value: string; unit: string; range: string; status: "outside" | "within" | "unknown"; status_text: string; page: number | null };
export type ReportBlock = { document: { id: string; filename: string; type: string; date: string; review_status: string }; items: ReportItem[]; extraction_warnings?: string[] };

export type Card = {
  v: number; route: string; title: string; summary: string; sections: CardSection[];
  context?: Record<string, unknown>;
  escalation?: Escalation; noted?: string[]; terms?: { term: string; plain: string }[]; follow_ups?: FollowUp[]; actions?: CardAction[];
  review?: Review | null; limits?: string[]; sources?: CardSourceRef[]; attributions?: string[]; report?: ReportBlock;
};

/** A source as stored with a message: the chips older screens show, plus (at most one) card. */
export type Source = {
  kind: string; document_id?: string; page?: number; excerpt?: string; title?: string; quote_id?: string; analysis_id?: string; observation_id?: string; label?: string; index?: number;
  publisher?: string; url?: string; source_id?: string; card?: Card;
};

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** A card from the server, made safe to draw: lists are lists, text is text, and an action that cannot work (no text, route or link) is dropped rather than shown as a dead button. */
export function parseCard(raw: unknown): Card | null {
  const c = raw as Partial<Card> | null;
  if (!c || typeof c !== "object" || typeof c.route !== "string") return null;
  const actions = arr<CardAction>(c.actions).filter(usable);
  return {
    ...(c as Card),
    title: typeof c.title === "string" ? c.title : "", summary: typeof c.summary === "string" ? c.summary : "",
    sections: arr<CardSection>(c.sections).filter((s) => s && arr(s.items).length > 0),
    follow_ups: arr<FollowUp>(c.follow_ups).filter((q) => q && q.text && arr(q.choices).length > 0),
    actions, terms: arr(c.terms), noted: arr(c.noted), limits: arr(c.limits), sources: arr(c.sources), attributions: arr(c.attributions),
    escalation: c.escalation ? { ...c.escalation, actions: arr<CardAction>(c.escalation.actions).filter(usable) } : undefined,
  };
}

export function usable(a: CardAction): boolean {
  if (!a || typeof a.id !== "string" || typeof a.label !== "string" || !a.label) return false;
  if (a.kind === "message") return !!a.text;
  if (a.kind === "navigate") return !!a.route && a.route.startsWith("/");
  if (a.kind === "external") return !!a.href && /^(https:\/\/|tel:|mailto:)/.test(a.href);
  if (a.kind === "confirm") return !!a.proposal_id;
  if (a.kind === "report") return !!a.document_id;
  return false;
}

/** The card stored with a message, and the other sources (the chips older screens show). */
export function splitSources(sources?: Source[]): { card: Card | null; chips: Source[] } {
  const list = sources ?? [];
  const k = list.find((s) => s.kind === "card");
  return { card: k ? parseCard(k.card) : null, chips: list.filter((s) => s.kind !== "card") };
}

export type Starter = CardAction;
/** The suggestions shown before the first message. Each one sends a real question or opens a real screen. If the server cannot be reached, the built-in set below is shown instead (it does the same things). */
export const FALLBACK_STARTERS: Starter[] = [
  { id: "s-routine", label: "Suggest a gentle skincare routine", kind: "message", text: "Suggest a gentle skincare routine" },
  { id: "s-sunscreen", label: "How do I choose a sunscreen?", kind: "message", text: "How do I choose and apply a sunscreen?" },
  { id: "s-acne", label: "I have pimples. What can help?", kind: "message", text: "I have pimples. What can help?" },
  { id: "take-photo", label: "Take or upload a photo", kind: "navigate", route: "/photo" },
  { id: "upload-report", label: "Upload a medical report", kind: "navigate", route: "/reports" },
  { id: "book", label: "Book a dermatologist appointment", kind: "navigate", route: "/book" },
];

/** "What are you noticing?": the choices offered after a photo, each one a sentence the person could have typed. Same wording as the server's (compose.concern_choices). */
export const FALLBACK_CONCERNS: FollowUp = {
  id: "concern", text: "What are you noticing?", choices: [
    { label: "Pimples or bumps", reply: "I have pimples or bumps on my skin." },
    { label: "Dark marks or uneven tone", reply: "I have dark marks or uneven skin tone." },
    { label: "Redness or irritation", reply: "My skin is red and irritated." },
    { label: "Dryness or flaking", reply: "My skin is dry and flaky." },
    { label: "An itchy rash", reply: "I have an itchy rash." },
    { label: "A mole or spot that changed", reply: "I have a mole that has changed." },
  ],
};
export type StarterSet = { starters: Starter[]; intro: string; library: { available: boolean; mode: string; approved: number; labelled_reference: number } | null; concerns: FollowUp };
const FALLBACK_INTRO = "I can explain skin and skincare topics in plain language, check the quality of a photo and explain what it can and cannot show, explain a medical report you upload, and help you with the clinic's services and appointments. I cannot diagnose or choose medicines.";

export async function loadStarters(): Promise<StarterSet> {
  try {
    const r = await api<{ starters: CardAction[]; intro?: string; library?: StarterSet["library"]; concerns?: FollowUp }>("/api/v1/assistant/starters");
    const s = arr<CardAction>(r.starters).filter(usable);
    return { starters: s.length ? s : FALLBACK_STARTERS, intro: typeof r.intro === "string" && r.intro ? r.intro : FALLBACK_INTRO, library: r.library ?? null,
      concerns: r.concerns && arr(r.concerns.choices).length ? r.concerns : FALLBACK_CONCERNS };
  } catch { return { starters: FALLBACK_STARTERS, intro: FALLBACK_INTRO, library: null, concerns: FALLBACK_CONCERNS }; }
}

/** What the person's own words and a card's text look like when read aloud: the card as plain text, in reading order. */
export function cardToSpeech(c: Card): string {
  const out: string[] = [];
  if (c.escalation) out.push(c.escalation.message || c.escalation.reason);
  if (c.title) out.push(c.title);
  if (c.summary) out.push(c.summary);
  for (const s of c.sections) { if (s.title) out.push(s.title); out.push(...s.items); }
  return out.filter(Boolean).join(". ").slice(0, 1500);
}
