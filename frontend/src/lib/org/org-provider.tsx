"use client";

import { type QueryClient, useQuery } from "@tanstack/react-query";
import { createContext, use, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, unwrap, type Organization } from "@/lib/api/client";
import { session } from "@/lib/auth/session";
import { queryKeys } from "@/lib/query-keys";

const STORAGE_KEY = "supportiq.activeOrganizationId";

type OrgContextValue = {
  organizations: Organization[];
  activeOrganization: Organization | null;
  isLoading: boolean;
  /** A (re)fetch is in flight: cached data may be stale, so don't redirect on it. */
  isFetching: boolean;
  isError: boolean;
  selectOrganization: (id: string) => void;
};

const OrgContext = createContext<OrgContextValue | null>(null);

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Make ``id`` the active organization on the next app load (e.g. after joining). */
export function rememberActiveOrganization(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Not persisted; the app falls back to the first organization.
  }
}

/**
 * Refetch the organization list after membership changes (create, join, leave).
 * refetchType "all" matters: with Cache Components, previously visited routes stay
 * mounted but hidden (React <Activity>), so their queries count as *inactive* and a
 * plain invalidate would leave their cached list stale until after navigation.
 */
export function refreshOrganizations(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: queryKeys.organizations, refetchType: "all" });
}

export function useOrganizationsQuery() {
  return useQuery({
    queryKey: queryKeys.organizations,
    queryFn: () =>
      unwrap(api.GET("/api/v1/organizations", { params: { query: { limit: 100 } } })),
  });
}

/** The organizations the user belongs to, and which one they are working in. */
export function OrgProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, isFetching, isError } = useOrganizationsQuery();
  // Only rendered in the browser (behind <AuthGuard>), so storage is available here.
  const [selectedId, setSelectedId] = useState<string | null>(readStoredId);

  const organizations = useMemo(() => data?.items ?? [], [data]);
  const activeOrganization =
    organizations.find((org) => org.id === selectedId) ?? organizations[0] ?? null;

  useEffect(() => {
    session.activeOrganizationId = activeOrganization?.id ?? null;
  }, [activeOrganization]);

  const selectOrganization = useCallback((id: string) => {
    setSelectedId(id);
    rememberActiveOrganization(id);
  }, []);

  const value = useMemo(
    () => ({
      organizations,
      activeOrganization,
      isLoading,
      isFetching,
      isError,
      selectOrganization,
    }),
    [organizations, activeOrganization, isLoading, isFetching, isError, selectOrganization],
  );
  return <OrgContext value={value}>{children}</OrgContext>;
}

export function useOrganizations(): OrgContextValue {
  const context = use(OrgContext);
  if (!context) throw new Error("useOrganizations must be used within <OrgProvider>");
  return context;
}

/** For pages rendered only behind <RequireOrganization>. */
export function useActiveOrganization(): Organization {
  const { activeOrganization } = useOrganizations();
  if (!activeOrganization) {
    throw new Error("useActiveOrganization must be used behind <RequireOrganization>");
  }
  return activeOrganization;
}
