import type { NextConfig } from "next";

// The browser only ever talks to this Next.js origin. Requests to /api/* are
// proxied to FastAPI, so the API is same-origin: the httpOnly refresh-token
// cookie (scoped to /api/v1/auth) just works, and no CORS is involved.
// Use 127.0.0.1, not localhost: on Windows, localhost tries IPv6 first (~2s stall).
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The Docker image runs the self-contained server (.next/standalone); `next start`
  // and `next dev` don't use it.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  experimental: {
    // The /api proxy below gives up on a request after 30 s by default. Answers from
    // a local model can take minutes (the API's LLM_TIMEOUT_SECONDS stays below this).
    proxyTimeout: 300_000,
    instantInsights: {
      // Validate instant navigation only where a segment opts in (`instant = true`).
      // The signed-in app renders behind a client-side session check (the access
      // token lives in browser memory), so by design nothing below <AuthGuard> can
      // render on the server; the default (validate every page) would flag them all.
      validationLevel: "manual-warning",
    },
  },
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
