/**
 * Browser session state for the API.
 *
 * - The access token lives only in memory (never localStorage), so page scripts
 *   injected via XSS can't read it from storage. It is short-lived (15 min).
 * - The refresh token is an httpOnly cookie scoped to /api/v1/auth; JavaScript
 *   never sees it. Calling /auth/refresh rotates it and returns a new access token.
 * - Refresh tokens are single-use: presenting a rotated one ends the session. So
 *   refreshes are de-duplicated within a tab (one in-flight promise) and
 *   serialized across tabs (Web Locks), and React StrictMode's double effects or
 *   two open tabs can't trigger reuse detection.
 */
import type { components } from "@/lib/api/schema";

type TokenResponse = components["schemas"]["TokenResponse"];

const REFRESH_URL = "/api/v1/auth/refresh";
const REFRESH_MARGIN_MS = 60_000; // refresh a minute before the access token expires

let accessToken: string | null = null;
let expiresAt = 0;
let activeOrganizationId: string | null = null;
let inFlightRefresh: Promise<boolean> | null = null;
const expiredListeners = new Set<() => void>();

function setTokens(token: string, expiresInSeconds: number): void {
  accessToken = token;
  expiresAt = Date.now() + expiresInSeconds * 1000;
}

function clear(): void {
  accessToken = null;
  expiresAt = 0;
}

async function requestRefresh(): Promise<boolean> {
  const response = await fetch(REFRESH_URL, { method: "POST", credentials: "same-origin" });
  if (!response.ok) {
    clear();
    return false;
  }
  const body = (await response.json()) as TokenResponse;
  setTokens(body.access_token, body.expires_in);
  return true;
}

/** Hold a cross-tab lock while refreshing, so tabs never present the same token. */
async function refreshWithLock(): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.locks) return requestRefresh();
  let refreshed = false;
  await navigator.locks.request("supportiq-auth-refresh", async () => {
    refreshed = await requestRefresh();
  });
  return refreshed;
}

/** Exchange the refresh cookie for a new access token. Resolves to success. */
function refresh(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh;
  const settled = refreshWithLock()
    .catch(() => {
      clear();
      return false;
    })
    .finally(() => {
      inFlightRefresh = null;
    });
  inFlightRefresh = settled;
  return settled;
}

export const session = {
  setTokens,
  clear,
  refresh,

  /** A usable access token, refreshing it first if it is about to expire. */
  async getAccessToken(): Promise<string | null> {
    if (!accessToken) return null;
    if (Date.now() < expiresAt - REFRESH_MARGIN_MS) return accessToken;
    return (await refresh()) ? accessToken : null;
  },

  /** Sent as X-Organization-Id for tenant-scoped endpoints. */
  get activeOrganizationId(): string | null {
    return activeOrganizationId;
  },
  set activeOrganizationId(id: string | null) {
    activeOrganizationId = id;
  },

  /** The API rejected our credentials: drop them and notify listeners. */
  expire(): void {
    clear();
    expiredListeners.forEach((listener) => listener());
  },

  onExpired(listener: () => void): () => void {
    expiredListeners.add(listener);
    return () => expiredListeners.delete(listener);
  },
};
