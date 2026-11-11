type Page = { limit: number; offset?: number };
type SearchPage = Page & { search?: string };

/**
 * Query keys. Paginated keys include their page/filters, so different views never
 * share a cache entry; the key without them is a prefix for invalidation.
 * Tenant data is keyed under its organization, so switching organizations never
 * shows another organization's cached data.
 */
export const queryKeys = {
  organizations: ["organizations"] as const,
  members: (orgId: string, page?: Page) =>
    page ? (["organizations", orgId, "members", page] as const) : (["organizations", orgId, "members"] as const),
  invitations: (orgId: string, page?: Page) =>
    page
      ? (["organizations", orgId, "invitations", page] as const)
      : (["organizations", orgId, "invitations"] as const),

  tickets: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "tickets", "list", filters] as const)
      : (["organizations", orgId, "tickets"] as const),
  ticketCounts: (orgId: string) => ["organizations", orgId, "tickets", "counts"] as const,
  ticket: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId] as const,
  ticketMessages: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "messages"] as const,
  ticketEvents: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "events"] as const,

  customers: (orgId: string, page?: SearchPage) =>
    page
      ? (["organizations", orgId, "customers", "list", page] as const)
      : (["organizations", orgId, "customers"] as const),
  customer: (orgId: string, customerId: string) =>
    ["organizations", orgId, "customers", "detail", customerId] as const,

  documents: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "documents", "list", filters] as const)
      : (["organizations", orgId, "documents"] as const),
  document: (orgId: string, documentId: string) =>
    ["organizations", orgId, "documents", "detail", documentId] as const,
  documentChunks: (orgId: string, documentId: string, page: Page) =>
    ["organizations", orgId, "documents", "detail", documentId, "chunks", page] as const,
  knowledgeSearch: (orgId: string, query: string) =>
    ["organizations", orgId, "documents", "search", query] as const,

  adminUsers: (page?: SearchPage) =>
    page ? (["admin", "users", page] as const) : (["admin", "users"] as const),
  adminOrganizations: (page?: SearchPage) =>
    page ? (["admin", "organizations", page] as const) : (["admin", "organizations"] as const),
};
