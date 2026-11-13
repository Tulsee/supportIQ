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
  ticketSla: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "sla"] as const,
  // Under the ticket, so live ticket updates refetch it too.
  ticketRouting: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "routing"] as const,
  ticketTriage: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "triage"] as const,
  ticketSelfService: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "self-service"] as const,

  assistantConversations: (orgId: string, page?: Page) =>
    page
      ? (["organizations", orgId, "assistant", "list", page] as const)
      : (["organizations", orgId, "assistant"] as const),
  assistantConversation: (orgId: string, conversationId: string) =>
    ["organizations", orgId, "assistant", "detail", conversationId] as const,

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

  aiRuns: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "ai-runs", "list", filters] as const)
      : (["organizations", orgId, "ai-runs"] as const),
  aiRun: (orgId: string, runId: string) =>
    ["organizations", orgId, "ai-runs", "detail", runId] as const,
  aiModel: (orgId: string) => ["organizations", orgId, "ai-model"] as const,
  aiFeedback: (orgId: string, runId: string) =>
    ["organizations", orgId, "ai-runs", "detail", runId, "feedback"] as const,
  ticketAI: (orgId: string, ticketId: string) =>
    ["organizations", orgId, "tickets", "detail", ticketId, "ai"] as const,
  analytics: (orgId: string, report: string, days: number) =>
    ["organizations", orgId, "analytics", report, days] as const,

  sla: (orgId: string) => ["organizations", orgId, "sla"] as const,
  categories: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "categories", filters] as const)
      : (["organizations", orgId, "categories"] as const),
  agents: (orgId: string) => ["organizations", orgId, "agents"] as const,
  teams: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "teams", filters] as const)
      : (["organizations", orgId, "teams"] as const),
  routingRules: (orgId: string) => ["organizations", orgId, "routing", "rules"] as const,
  customerFields: (orgId: string) => ["organizations", orgId, "routing", "customer-fields"] as const,
  agentMe: (orgId: string) => ["organizations", orgId, "agents", "me"] as const,
  notifications: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "notifications", "list", filters] as const)
      : (["organizations", orgId, "notifications"] as const),
  notificationPreferences: (orgId: string) =>
    ["organizations", orgId, "notifications", "preferences"] as const,
  auditLog: (orgId: string, filters?: object) =>
    filters
      ? (["organizations", orgId, "audit-log", filters] as const)
      : (["organizations", orgId, "audit-log"] as const),
  adminUsers: (page?: SearchPage) =>
    page ? (["admin", "users", page] as const) : (["admin", "users"] as const),
  adminOrganizations: (page?: SearchPage) =>
    page ? (["admin", "organizations", page] as const) : (["admin", "organizations"] as const),
};
