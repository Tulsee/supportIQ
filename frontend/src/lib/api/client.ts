/**
 * Typed API client, generated from the backend's OpenAPI contract
 * (`npm run gen:api` regenerates src/lib/api/schema.d.ts).
 *
 * Paths, params, bodies and responses are all type-checked against the backend.
 */
import createClient, { type Middleware } from "openapi-fetch";

import { session } from "@/lib/auth/session";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type User = Schemas["UserRead"];
export type Organization = Schemas["OrganizationWithRole"];
export type OrgRole = Organization["role"];
export type Member = Schemas["MemberRead"];
export type Invitation = Schemas["InvitationRead"];
export type InvitationPreview = Schemas["InvitationPreview"];
export type Customer = Schemas["CustomerRead"];
export type Ticket = Schemas["TicketRead"];
export type TicketStatus = Ticket["status"];
export type TicketPriority = Ticket["priority"];
export type TicketMessage = Schemas["MessageRead"];
export type TicketEvent = Schemas["EventRead"];
// "Document" would shadow the DOM type.
export type KnowledgeDocument = Schemas["DocumentRead"];
export type DocumentChunk = Schemas["ChunkRead"];
export type AIRun = Schemas["AIRunRead"];
export type AIRunSummary = Schemas["AIRunSummary"];
export type RetrievedPassage = Schemas["RetrievedPassage"];
export type Answer = Schemas["AnswerRead"];
export type AnswerStatus = Answer["status"];
export type AnswerCitation = Schemas["CitationRead"];
export type ModelStatus = Schemas["ModelStatusRead"];
export type AppNotification = Schemas["NotificationRead"];
export type NotificationKind = Schemas["NotificationKind"];
export type NotificationDelivery = Schemas["NotificationDelivery"];
export type AuditEntry = Schemas["AuditEntryRead"];
export type Category = Schemas["CategoryRead"];
export type Team = Schemas["TeamRead"];
export type Agent = Schemas["AgentRead"];
export type AgentStatus = Agent["status"];
export type AgentPresence = Agent["presence"];
export type SkillLevel = Schemas["SkillLevel-Output"];
export type RoutingRule = Schemas["RuleRead"];
export type RoutingStrategy = RoutingRule["strategy"];
export type RoutingCondition = Schemas["ConditionIn"];
export type RoutingDecision = Schemas["DecisionRead"];
export type RoutingSimulation = Schemas["SimulationRead"];
export type RoutingOutcome = RoutingDecision["outcome"];
export type RoutingCandidate = Schemas["CandidateRead"];
export type RoutingMode = Schemas["OrganizationSettings"]["routing_mode"];
export type TicketSlaSummary = Schemas["TicketSlaSummary"];
export type TicketTriage = Schemas["TriageRead"];
export type TriageMode = Schemas["OrganizationSettings"]["triage_mode"];
export type OrganizationSettings = Schemas["OrganizationSettings"];
export type AssistantConversation = Schemas["AssistantConversationRead"];
export type AssistantConversationSummary = Schemas["AssistantConversationSummary"];
export type AssistantMessage = Schemas["AssistantMessageRead"];
export type AssistantSource = Schemas["AssistantSource"];
export type StaffAssistantConversation = Schemas["StaffAssistantConversationRead"];
export type StaffAssistantMessage = Schemas["StaffAssistantMessageRead"];

/**
 * The tenant header for organization-scoped endpoints (customers, tickets, ...).
 * Passed explicitly with each call (the schema marks it required), so a request
 * can never go out with another organization's id.
 */
export function tenant(organizationId: string): { "X-Organization-Id": string } {
  return { "X-Organization-Id": organizationId };
}

/** An error response from the API, decoded from its `{ error: {...} }` envelope. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Endpoints that establish or end a session: they never take the access token, and
// their 401s (wrong password, missing cookie) don't mean an active session ended.
const SESSION_ENDPOINTS = new Set(
  ["login", "register", "refresh", "logout"].map((name) => `/api/v1/auth/${name}`),
);
const isSessionEndpoint = (request: Request) =>
  SESSION_ENDPOINTS.has(new URL(request.url).pathname);

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    if (!isSessionEndpoint(request)) {
      const token = await session.getAccessToken();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
  async onResponse({ request, response }) {
    // Tokens are refreshed proactively before they expire, so a 401 here means
    // the session was revoked (logout elsewhere, deactivated account).
    if (response.status === 401 && !isSessionEndpoint(request)) session.expire();
    return response;
  },
};

// Relative base URL: requests go to this Next.js origin, which proxies /api/*.
export const api = createClient<paths>({ baseUrl: "" });
api.use(authMiddleware);

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/** Await an openapi-fetch call and return its data, or throw an ApiError. */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  const envelope = (error as Schemas["ErrorResponse"] | undefined)?.error;
  const retryAfter = Number(response.headers.get("Retry-After")) || undefined;
  throw new ApiError(
    response.status,
    envelope?.code ?? "unknown_error",
    envelope?.message ?? `Request failed (${response.status})`,
    envelope?.details,
    retryAfter,
  );
}

/** A user-facing message for any thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.retryAfterSeconds) {
      const minutes = Math.ceil(error.retryAfterSeconds / 60);
      return `${error.message} (about ${minutes} minute${minutes === 1 ? "" : "s"}).`;
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}
