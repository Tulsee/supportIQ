"use client";

import { ShieldAlertIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useAuth, useCurrentUser } from "@/lib/auth/auth-provider";
import { useActiveOrganization, useOrganizations } from "@/lib/org/org-provider";

/**
 * Renders children only for a signed-in user; otherwise sends them to sign in and
 * back here afterwards. This is a UX guard: the API authorizes every request.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status !== "anonymous") return;
    // Read at redirect time rather than via usePathname(): that hook suspends on
    // dynamic routes under Cache Components, and this also keeps the query string.
    const here = window.location.pathname + window.location.search;
    router.replace(`/login?next=${encodeURIComponent(here)}`);
  }, [status, router]);

  if (status !== "authenticated") return <FullPageSpinner />;
  return children;
}

/** Renders children once an active organization exists; otherwise starts onboarding. */
export function RequireOrganization({ children }: { children: ReactNode }) {
  const { activeOrganization, isLoading, isFetching, isError } = useOrganizations();
  const router = useRouter();
  // Decide only on fresh data: a cached empty list can predate a just-created org.
  const needsOnboarding = !isLoading && !isFetching && !isError && !activeOrganization;

  useEffect(() => {
    if (needsOnboarding) router.replace("/onboarding");
  }, [needsOnboarding, router]);

  if (isError) {
    return (
      <div className="mx-auto max-w-md p-6">
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load your organizations</AlertTitle>
          <AlertDescription>Check your connection and reload the page.</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (!activeOrganization) return <FullPageSpinner className="min-h-[50svh]" />;
  return children;
}

/** Pages for an organization's admins and agents. A UX guard: the API checks too. */
export function RequireStaff({ children }: { children: ReactNode }) {
  const organization = useActiveOrganization();
  if (organization.role === "customer") {
    return (
      <div className="mx-auto max-w-md p-6">
        <Alert>
          <ShieldAlertIcon aria-hidden />
          <AlertTitle>For the support team</AlertTitle>
          <AlertDescription>
            This page is only available to admins and agents of {organization.name}.
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  return children;
}

/** Platform administration pages. A UX guard: the API requires super admin too. */
export function RequireSuperAdmin({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  if (!user.is_superadmin) {
    return (
      <div className="mx-auto max-w-md p-6">
        <Alert>
          <ShieldAlertIcon aria-hidden />
          <AlertTitle>Platform administrators only</AlertTitle>
          <AlertDescription>
            Your account doesn&apos;t have access to platform administration.
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  return children;
}
