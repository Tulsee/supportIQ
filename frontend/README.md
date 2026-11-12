# SupportIQ web app

The Next.js front end of SupportIQ. See the [main README](../README.md) for the whole
project, and [docs/architecture.md](../docs/architecture.md) for how it fits together.

```bash
npm ci
npm run dev        # http://localhost:3000, with the API on http://127.0.0.1:8000
npm run lint
npm run typecheck
npm run gen:api    # regenerate src/lib/api/schema.d.ts after changing the API
```

- **One origin.** The browser only talks to this app; `/api/*` is proxied to FastAPI
  (`BACKEND_URL`, default `http://127.0.0.1:8000`). In the Docker setup, Caddy routes
  `/api` instead.
- **Typed API client.** `openapi-fetch` with types generated from the backend's OpenAPI
  document (`src/lib/api/`). CI fails if they're out of date.
- **Data.** TanStack Query; query keys live in `src/lib/query-keys.ts`. Pages stay
  mounted while hidden (Cache Components), so invalidations that must reach them use
  `refetchType: "all"`.
- **Auth.** The access token lives in memory; the refresh token is an httpOnly cookie
  scoped to `/api/v1/auth` (`src/lib/auth/`).
- **UI.** shadcn/ui on Base UI, Tailwind CSS 4, light and dark themes.
- **Docker.** `Dockerfile` builds the standalone server (`NEXT_OUTPUT=standalone`).
