"use client";

import { useEffect, useState } from "react";
import { blob } from "@/lib/api";

/** A signed-in download of a private file (a photo) as an object URL for <img>. The request carries the person's own sign-in; the file's real location is never exposed. Revoked on change and unmount. */
export function useBlobUrl(path: string | null | undefined): { url: string; failed: boolean } {
  const [state, setState] = useState<{ path: string; url: string; failed: boolean }>({ path: "", url: "", failed: false });
  useEffect(() => {
    if (!path) return;
    let live = true, made = "";
    blob(path).then((b) => { if (!live) return; made = URL.createObjectURL(b); setState({ path, url: made, failed: false }); })
      .catch(() => { if (live) setState({ path, url: "", failed: true }); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [path]);
  return path && state.path === path ? { url: state.url, failed: state.failed } : { url: "", failed: false };
}
