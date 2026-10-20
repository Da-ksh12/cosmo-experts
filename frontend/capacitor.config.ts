import fs from "node:fs";
import type { CapacitorConfig } from "@capacitor/cli";

// Settings come from android.config.json (committed defaults, no secrets) overridden by android.config.local.json (personal, ignored by Git).
const read = (file: string): Record<string, unknown> => (fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : {});
const c = { appId: "ai.cosmoexperts.app", appName: "Cosmo Experts AI", androidScheme: "http", ...read("android.config.json"), ...read("android.config.local.json") } as {
  appId: string; appName: string; androidScheme: "http" | "https";
};

const IVORY = "#faf7f1";

const config: CapacitorConfig = {
  appId: c.appId,
  appName: c.appName,
  webDir: "out-android",                       // the static export of the real UI (scripts/build-android.mjs)
  android: {
    path: "../android",                        // the Android project lives at the repository root, next to frontend/
    backgroundColor: IVORY,
    allowMixedContent: false,                  // an https page never talks to http, and the http scheme below never loads https content insecurely
  },
  server: {
    // The app is served from http://localhost inside the WebView. That lets a debug build reach a development backend on the local network over plain http (Android still blocks
    // every host that is not listed in the debug network-security config) without a mixed-content block. For a release build against an https server, set "androidScheme": "https".
    androidScheme: c.androidScheme,
  },
  plugins: {
    SplashScreen: { launchAutoHide: true, launchShowDuration: 1200, launchFadeOutDuration: 250, backgroundColor: IVORY, showSpinner: false, androidScaleType: "CENTER_INSIDE" },
    SystemBars: { insetsHandling: "css", style: "LIGHT" },     // dark icons on the light ivory pages; safe-area insets are provided to the CSS
    Keyboard: { resize: "native" },
  },
};

export default config;
