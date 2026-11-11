type Page = { limit: number; offset?: number };
type SearchPage = Page & { search?: string };

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
  adminUsers: (page?: SearchPage) =>
    page ? (["admin", "users", page] as const) : (["admin", "users"] as const),
  adminOrganizations: (page?: SearchPage) =>
    page ? (["admin", "organizations", page] as const) : (["admin", "organizations"] as const),
};
