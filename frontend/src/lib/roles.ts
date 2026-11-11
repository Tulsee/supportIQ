import type { OrgRole } from "@/lib/api/client";

export const ROLE_LABELS: Record<OrgRole, string> = {
  admin: "Admin",
  agent: "Agent",
  customer: "Customer",
};

export const ROLE_DESCRIPTIONS: Record<OrgRole, string> = {
  admin: "Manages the organization, members, and settings",
  agent: "Handles tickets and customer conversations",
  customer: "Creates tickets and sees their own conversations",
};

export const ROLES = Object.keys(ROLE_LABELS) as OrgRole[];

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts.at(-1)![0] : (parts[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}
