/**
 * An invitation the user is in the middle of accepting, kept across the
 * sign-in / sign-up detour. sessionStorage (this tab only, cleared when it
 * closes) rather than a URL parameter, so the token never appears in a URL that
 * the server could log.
 */
const KEY = "supportiq.pendingInvitation";

export type PendingInvitation = { token: string; email?: string };

export function savePendingInvitation(invitation: PendingInvitation): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(invitation));
  } catch {
    // Storage unavailable (private mode, blocked): the flow still works in-page.
  }
}

export function readPendingInvitation(): PendingInvitation | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PendingInvitation) : null;
  } catch {
    return null;
  }
}

export function clearPendingInvitation(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
