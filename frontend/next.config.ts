import type { NextConfig } from "next";

const API = process.env.COSMO_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Same-origin proxy: the browser never needs the backend URL, and no secret ever reaches the client.
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

export default nextConfig;
