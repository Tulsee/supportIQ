import { readPendingInvitation } from "@/lib/invitations/pending";

/** Only same-site relative paths, so `?next=` can't redirect to another origin. */
export function safeNextPath(next: string | null | undefined): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return null;
  }
  return next;
}

/** Where to go after signing in or registering. */
export function postAuthDestination(next: string | null | undefined): string {
  if (readPendingInvitation()) return "/invitations/accept";
  return safeNextPath(next) ?? "/dashboard";
}
