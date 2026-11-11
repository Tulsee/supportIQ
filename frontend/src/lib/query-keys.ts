type Page = { limit: number; offset?: number };

/**
 * Query keys. Paginated keys include their page, so differently-sized pages never
 * share a cache entry; the key without a page is a prefix for invalidation.
 */
export const queryKeys = {
  organizations: ["organizations"] as const,
  members: (orgId: string, page?: Page) =>
    page ? (["organizations", orgId, "members", page] as const) : (["organizations", orgId, "members"] as const),
  invitations: (orgId: string, page?: Page) =>
    page
      ? (["organizations", orgId, "invitations", page] as const)
      : (["organizations", orgId, "invitations"] as const),
};
