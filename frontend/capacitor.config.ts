import fs from "node:fs";
import type { CapacitorConfig } from "@capacitor/cli";

// Settings come from android.config.json (committed defaults, no secrets) overridden by android.config.local.json (personal, ignored by Git).
const read = (file: string): Record<string, unknown> => (fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : {});
const c = { appId: "ai.cosmoexperts.app", appName: "Cosmo Experts AI", androidScheme: "https", ...read("android.config.json"), ...read("android.config.local.json") } as {
  appId: string; appName: string; androidScheme: "http" | "https";
};

const IVORY = "#faf7f1";
const release = process.env.COSMO_RELEASE === "1";        // set by scripts/build-android.mjs --release

const config: CapacitorConfig = {
  appId: c.appId,
  appName: c.appName,
  webDir: "out-android",                       // the static export of the real UI (scripts/build-android.mjs)
  android: {
    path: "../android",                        // the Android project lives at the repository root, next to frontend/
    backgroundColor: IVORY,
    allowMixedContent: false,                  // an https page never talks to http, and the http scheme below never loads https content insecurely
    // A release app can not be inspected through Chrome DevTools and writes nothing from the web view to the device log (patient data must not end up in logcat).
    ...(release ? { webContentsDebuggingEnabled: false, loggingBehavior: "none" as const } : {}),
  },
  server: {
    // The app's screens are served from the APK itself at https://localhost (an internal address that never touches the network), so it opens with no server running anywhere.
    // scripts/build-android.mjs sets COSMO_ANDROID_SCHEME=http only for a --dev-server development build, so that build can reach its development host without a mixed-content block.
    androidScheme: (process.env.COSMO_ANDROID_SCHEME as "http" | "https" | undefined) ?? c.androidScheme,
  },
  plugins: {
    SplashScreen: { launchAutoHide: true, launchShowDuration: 1200, launchFadeOutDuration: 250, backgroundColor: IVORY, showSpinner: false, androidScaleType: "CENTER_INSIDE" },
    SystemBars: { insetsHandling: "css", style: "LIGHT" },     // dark icons on the light ivory pages; safe-area insets are provided to the CSS
    Keyboard: { resize: "native" },
  },
};

export default config;
