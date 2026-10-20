import type { NextConfig } from "next";

const API = process.env.COSMO_API_URL ?? "http://127.0.0.1:8000";
const ANDROID = process.env.NEXT_PUBLIC_TARGET === "android";

// One source tree, two builds.
//  * Web (default): a server build. The browser calls /api on its own origin and this proxy forwards it, so the browser never needs the backend URL and no secret reaches the client.
//  * Android (NEXT_PUBLIC_TARGET=android, see scripts/build-android.mjs): a static export that Capacitor bundles into the app. It has no server, so it calls an explicit API address
//    (lib/config.ts). Files named page.web.tsx are pages only in the web build (the per-report page cannot be exported; the app uses /reports/view/?id=...).
const web: NextConfig = {
  pageExtensions: ["tsx", "ts", "jsx", "js", "web.tsx"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=()" },
      ],
    }];
  },
};

const android: NextConfig = {
  output: "export",
  distDir: "out-android",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default ANDROID ? android : web;
