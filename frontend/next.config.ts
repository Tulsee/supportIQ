import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin. Requests to /api/* are
// proxied to FastAPI, so the API is same-origin: the httpOnly refresh-token
// cookie (scoped to /api/v1/auth) just works, and no CORS is involved.
// Use 127.0.0.1, not localhost: on Windows, localhost tries IPv6 first (~2s stall).
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
