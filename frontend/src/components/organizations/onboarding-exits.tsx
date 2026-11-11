"use client";

import { ArrowLeftIcon, ShieldIcon } from "lucide-react";
import Link from "next/link";

import { useCurrentUser } from "@/lib/auth/auth-provider";
import { useOrganizationsQuery } from "@/lib/org/org-provider";

/** Ways out of onboarding for people who don't need a new organization. */
export function OnboardingExits() {
  const user = useCurrentUser();
  const { data } = useOrganizationsQuery();
  const hasOrganizations = (data?.total ?? 0) > 0;
  if (!hasOrganizations && !user.is_superadmin) return null;

  const linkClass =
    "inline-flex items-center gap-1.5 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline";
  return (
    <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm">
      {hasOrganizations ? (
        <Link href="/dashboard" className={linkClass}>
          <ArrowLeftIcon className="size-4" aria-hidden />
          Back to your workspace
        </Link>
      ) : null}
      {user.is_superadmin ? (
        <Link href="/admin/users" className={linkClass}>
          <ShieldIcon className="size-4" aria-hidden />
          Platform administration
        </Link>
      ) : null}
    </div>
  );
}
