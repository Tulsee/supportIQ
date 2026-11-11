"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useAuth } from "@/lib/auth/auth-provider";
import { useOrganizations } from "@/lib/org/org-provider";

/**
 * Renders children only for a signed-in user; otherwise sends them to sign in and
 * back here afterwards. This is a UX guard: the API authorizes every request.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === "anonymous") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [status, router, pathname]);

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
  if (!activeOrganization) return <FullPageSpinner />;
  return children;
}
