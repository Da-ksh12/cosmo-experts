"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

export type Region = "face" | "hair_scalp" | "body" | "other";

/** `source` is exactly what the person chose or took. `edited` exists only if they cropped or rotated it in the adjust step (and then it is what is sent). */
export type Shot = { key: string; source: File; edited: Blob | null };

type Ctx = {
  region: Region; setRegion: (r: Region) => void; shots: Shot[];
  add: (f: File) => string; replace: (key: string, f: File) => void; setEdited: (key: string, b: Blob | null) => void; remove: (key: string) => void; clear: () => void;
};
const FlowCtx = createContext<Ctx | null>(null);
export const usePhotoFlow = () => useContext(FlowCtx)!;

/** In-memory only: photos never touch local storage. They are sent to the server solely when the patient taps Continue. */
export function PhotoFlowProvider({ children }: { children: React.ReactNode }) {
  const [region, setRegion] = useState<Region>("face");
  const [shots, setShots] = useState<Shot[]>([]);
  const n = useRef(0);
  const add = useCallback((f: File) => { const key = `s${++n.current}`; setShots((s) => [...s, { key, source: f, edited: null }]); return key; }, []);
  const replace = useCallback((key: string, f: File) => setShots((s) => s.map((x) => (x.key === key ? { key, source: f, edited: null } : x))), []);
  const setEdited = useCallback((key: string, b: Blob | null) => setShots((s) => s.map((x) => (x.key === key ? { ...x, edited: b } : x))), []);
  const remove = useCallback((key: string) => setShots((s) => s.filter((x) => x.key !== key)), []);
  const clear = useCallback(() => setShots([]), []);
  const value = useMemo(() => ({ region, setRegion, shots, add, replace, setEdited, remove, clear }), [region, shots, add, replace, setEdited, remove, clear]);
  return <FlowCtx.Provider value={value}>{children}</FlowCtx.Provider>;
}
