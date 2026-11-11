"use client";

import { type QueryClient, useQuery } from "@tanstack/react-query";

import { api, unwrap, type Member } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

const MEMBERS_PAGE = { limit: 100 };

/**
 * Tickets change under other people's hands (a customer replies, a colleague
 * reassigns), so ticket queries refetch whenever a view mounts or the window
 * regains focus instead of trusting the app-wide 30 s freshness window.
 */
export const LIVE_QUERY = { staleTime: 0 } as const;

/** Admins and agents of the active organization: the people tickets can go to. */
export function useAssignableMembers(): { members: Member[]; isPending: boolean } {
  const organization = useActiveOrganization();
  const enabled = organization.role !== "customer";
  const query = useQuery({
    queryKey: queryKeys.members(organization.id, MEMBERS_PAGE),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/organizations/{org_id}/members", {
          params: { path: { org_id: organization.id }, query: MEMBERS_PAGE },
        }),
      ),
    enabled,
  });
  const members = (query.data?.items ?? []).filter((m) => m.role !== "customer");
  return { members, isPending: enabled && query.isPending };
}

/**
 * Refetch ticket data after a change: lists, counts, and the ticket itself.
 * refetchType "all" also refreshes routes kept mounted in the background.
 */
export function refreshTickets(queryClient: QueryClient, orgId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: queryKeys.tickets(orgId),
    refetchType: "all",
  });
}
