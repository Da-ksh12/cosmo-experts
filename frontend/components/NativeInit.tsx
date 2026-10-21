"use client";

import { useEffect } from "react";
import { IS_APP } from "@/lib/config";

/** Android app only. The system Back button (or gesture) first closes an open dialog, then goes back one screen, and leaves the app only from the first screen.
 *  Without this, a back press on a dialog would leave the screen underneath it, and the first screen would not exit cleanly. */
export default function NativeInit() {
  useEffect(() => {
    if (!IS_APP) return;
    let live = true;
    let remove: (() => void) | undefined;
    import("@capacitor/app").then(({ App }) => {
      void App.addListener("backButton", ({ canGoBack }) => {
        if (document.querySelector('[role="dialog"]')) {                          // a dialog is open: Back closes it (the dialog listens for Escape)
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          return;
        }
        const here = window.location.pathname.replace(/\/+$/, "") || "/";
        if (here === "/" || here === "/login" || here === "/clinic" || !canGoBack) { void App.exitApp(); return; }
        window.history.back();
      }).then((handle) => { if (live) remove = () => void handle.remove(); else void handle.remove(); });
    }).catch(() => { /* the plugin is not available (a browser): the browser's own Back button applies */ });
    return () => { live = false; remove?.(); };
  }, []);
  return null;
}
