"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, CircleCheckIcon, LockIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";

import { CloseTicketButton } from "@/components/tickets/close-ticket-button";
import { Conversation } from "@/components/tickets/conversation";
import {
  LIVE_QUERY,
  refreshTickets,
  useAssignableMembers,
  useCategories,
  useTeams,
} from "@/components/tickets/hooks";
import { NewRequestDialog } from "@/components/tickets/new-request-dialog";
import { ReplyBox, type ReplyDraft } from "@/components/tickets/reply-box";
import { RoutingPanel } from "@/components/tickets/routing-panel";
import { SelfServicePanel } from "@/components/tickets/self-service-panel";
import { TriagePanel } from "@/components/tickets/triage-panel";
import { SlaPanel } from "@/components/tickets/sla-panel";
import { TicketAIPanel } from "@/components/tickets/ticket-ai-panel";
import { PriorityLabel, StatusBadge } from "@/components/tickets/ticket-badges";
import { TicketTimeline } from "@/components/tickets/ticket-timeline";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  api,
  errorMessage,
  tenant,
  unwrap,
  type Schemas,
  type Ticket,
  type TicketPriority,
  type TicketStatus,
} from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { PRIORITIES, PRIORITY_LABELS, STATUS_LABELS, formatDateTime } from "@/lib/tickets";

const UNASSIGNED = "unassigned";
const NO_CATEGORY = "none";
const NO_TEAM = "none";
const MESSAGES_PAGE = { limit: 100 };

/** Staff controls: status (lifecycle-aware), assignee, priority. */
function TicketProperties({ ticket }: { ticket: Ticket }) {
  // Unique per instance: visited routes stay mounted (hidden), so two ticket
  // pages can be in the document at once.
  const uid = useId();
  const organization = useActiveOrganization();
  const user = useCurrentUser();
  const queryClient = useQueryClient();
  const { members } = useAssignableMembers();
  const { categories } = useCategories();
  const { teams } = useTeams();
  const closed = ticket.status === "closed";

  const update = useMutation({
    mutationFn: (body: Schemas["TicketUpdate"]) =>
      unwrap(
        api.PATCH("/api/v1/tickets/{ticket_id}", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
          body,
        }),
      ),
    onSuccess: async (updated) => {
      queryClient.setQueryData(queryKeys.ticket(organization.id, ticket.id), updated);
      await refreshTickets(queryClient, organization.id);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  // Changing or clearing someone's assignment asks why (optional, shown to staff in
  // the timeline); a first assignment just happens.
  const [reassignTo, setReassignTo] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const requestAssignee = (next: string | null) => {
    if (ticket.assignee && next !== ticket.assignee.id) {
      setReason("");
      setReassignTo(next ?? UNASSIGNED);
    } else {
      update.mutate({ assignee_id: next });
    }
  };
  const confirmReassign = () => {
    const next = reassignTo === UNASSIGNED ? null : reassignTo;
    update.mutate(
      { assignee_id: next, ...(reason.trim() ? { assignment_reason: reason.trim() } : {}) },
      { onSuccess: () => setReassignTo(null) },
    );
  };

  // Closing is final, so it has its own confirmed button (see CloseTicketButton).
  const changeTo = ticket.allowed_statuses.filter((status) => status !== "closed");
  const statusOptions: TicketStatus[] = [ticket.status, ...changeTo];
  const categoryItems: Record<string, string> = {
    [NO_CATEGORY]: "No category",
    ...Object.fromEntries(categories.map((c) => [c.id, c.name])),
  };
  // Keep the current category selectable even if it was deactivated since.
  if (ticket.category && !(ticket.category.id in categoryItems)) {
    categoryItems[ticket.category.id] = `${ticket.category.name} (inactive)`;
  }
  const teamItems: Record<string, string> = {
    [NO_TEAM]: "No team",
    ...Object.fromEntries(teams.map((t) => [t.id, t.name])),
  };
  // Keep the current team selectable even if it was turned off since.
  if (ticket.team && !(ticket.team.id in teamItems)) {
    teamItems[ticket.team.id] = `${ticket.team.name} (off)`;
  }
  const assigneeItems: Record<string, string> = {
    [UNASSIGNED]: "Unassigned",
    ...Object.fromEntries(members.map((m) => [m.user_id, m.full_name])),
  };
  // Keep the current assignee selectable even if they left the organization.
  if (ticket.assignee && !(ticket.assignee.id in assigneeItems)) {
    assigneeItems[ticket.assignee.id] = ticket.assignee.full_name;
  }

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2>Properties</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <FieldGroup className="gap-4">
          <Field>
            <FieldLabel htmlFor={`${uid}-status`}>Status</FieldLabel>
            <Select
              items={STATUS_LABELS}
              value={ticket.status}
              onValueChange={(value) => {
                if (value && value !== ticket.status) update.mutate({ status: value as TicketStatus });
              }}
              disabled={update.isPending || changeTo.length === 0}
            >
              <SelectTrigger id={`${uid}-status`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statusOptions.map((status) => (
                  <SelectItem key={status} value={status}>
                    {STATUS_LABELS[status]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {ticket.status === "open" ? (
              <FieldDescription>Assign someone to start work on it.</FieldDescription>
            ) : null}
          </Field>
          <Field>
            <div className="flex items-center justify-between">
              <FieldLabel htmlFor={`${uid}-assignee`}>Assignee</FieldLabel>
              {!closed && ticket.assignee?.id !== user.id ? (
                <Button
                  variant="link"
                  size="xs"
                  className="h-auto p-0"
                  disabled={update.isPending}
                  onClick={() => requestAssignee(user.id)}
                >
                  Assign to me
                </Button>
              ) : null}
            </div>
            <Select
              items={assigneeItems}
              value={ticket.assignee?.id ?? UNASSIGNED}
              onValueChange={(value) => {
                if (!value || value === (ticket.assignee?.id ?? UNASSIGNED)) return;
                requestAssignee(value === UNASSIGNED ? null : value);
              }}
              disabled={update.isPending || closed}
            >
              <SelectTrigger id={`${uid}-assignee`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(assigneeItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor={`${uid}-team`}>Team</FieldLabel>
            <Select
              items={teamItems}
              value={ticket.team?.id ?? NO_TEAM}
              onValueChange={(value) => {
                if (!value || value === (ticket.team?.id ?? NO_TEAM)) return;
                update.mutate({ team_id: value === NO_TEAM ? null : value });
              }}
              disabled={update.isPending || closed}
            >
              <SelectTrigger id={`${uid}-team`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(teamItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {teams.length === 0 && organization.role === "admin" ? (
              <FieldDescription>
                <Link href="/settings/teams" className="underline-offset-4 hover:underline">
                  Add teams
                </Link>{" "}
                to route tickets to them.
              </FieldDescription>
            ) : null}
          </Field>
          <Field>
            <FieldLabel htmlFor={`${uid}-category`}>Category</FieldLabel>
            <Select
              items={categoryItems}
              value={ticket.category?.id ?? NO_CATEGORY}
              onValueChange={(value) => {
                if (!value || value === (ticket.category?.id ?? NO_CATEGORY)) return;
                update.mutate({ category_id: value === NO_CATEGORY ? null : value });
              }}
              disabled={update.isPending || closed}
            >
              <SelectTrigger id={`${uid}-category`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(categoryItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {categories.length === 0 && organization.role === "admin" ? (
              <FieldDescription>
                <Link href="/settings/categories" className="underline-offset-4 hover:underline">
                  Add categories
                </Link>{" "}
                to sort tickets by topic.
              </FieldDescription>
            ) : null}
          </Field>
          <Field>
            <FieldLabel htmlFor={`${uid}-priority`}>Priority</FieldLabel>
            <Select
              items={PRIORITY_LABELS}
              value={ticket.priority}
              onValueChange={(value) => {
                if (value && value !== ticket.priority) {
                  update.mutate({ priority: value as TicketPriority });
                }
              }}
              disabled={update.isPending || closed}
            >
              <SelectTrigger id={`${uid}-priority`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIORITIES.map((priority) => (
                  <SelectItem key={priority} value={priority}>
                    {PRIORITY_LABELS[priority]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </FieldGroup>
      </CardContent>
      <Dialog open={reassignTo !== null} onOpenChange={(open) => !open && setReassignTo(null)}>
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              confirmReassign();
            }}
          >
            <DialogHeader>
              <DialogTitle>
                {reassignTo === UNASSIGNED
                  ? `Unassign ${ticket.assignee?.full_name}?`
                  : `Reassign to ${assigneeItems[reassignTo ?? ""] ?? "someone else"}?`}
              </DialogTitle>
              <DialogDescription>
                {ticket.assignee?.full_name} is working on #{ticket.number}. A reason helps
                whoever picks it up; it&apos;s shown to staff in the activity.
              </DialogDescription>
            </DialogHeader>
            <Field className="my-4">
              <FieldLabel htmlFor={`${uid}-reason`}>Reason (optional)</FieldLabel>
              <Textarea
                id={`${uid}-reason`}
                value={reason}
                maxLength={300}
                rows={3}
                placeholder="e.g. Needs a refund approval"
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setReassignTo(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={update.isPending}>
                {reassignTo === UNASSIGNED ? "Unassign" : "Reassign"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function TicketDetail() {
  const { id } = useParams<{ id: string }>();
  const organization = useActiveOrganization();
  const user = useCurrentUser();
  const audience = organization.role === "customer" ? "customer" : "staff";
  const header = tenant(organization.id);
  const [draft, setDraft] = useState<ReplyDraft | null>(null);

  const ticket = useQuery({
    queryKey: queryKeys.ticket(organization.id, id),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}", {
          params: { path: { ticket_id: id }, header },
        }),
      ),
  });
  const messages = useQuery({
    queryKey: queryKeys.ticketMessages(organization.id, id),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/messages", {
          params: { path: { ticket_id: id }, query: MESSAGES_PAGE, header },
        }),
      ),
    enabled: ticket.isSuccess,
  });
  const events = useQuery({
    queryKey: queryKeys.ticketEvents(organization.id, id),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/events", {
          params: { path: { ticket_id: id }, header },
        }),
      ),
    enabled: ticket.isSuccess,
  });

  const backLink = (
    <Link
      href="/tickets"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeftIcon className="size-4" aria-hidden />
      {audience === "customer" ? "My requests" : "Tickets"}
    </Link>
  );

  if (ticket.isPending) {
    return (
      <div className="space-y-4">
        {backLink}
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (ticket.isError) {
    const notFound = ticket.error instanceof ApiError && ticket.error.status === 404;
    return (
      <div className="space-y-4">
        {backLink}
        <Alert variant={notFound ? "default" : "destructive"}>
          <AlertTitle>{notFound ? "Ticket not found" : "Couldn't load this ticket"}</AlertTitle>
          <AlertDescription>
            {notFound
              ? "It may belong to another organization, or you don't have access to it."
              : errorMessage(ticket.error)}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = ticket.data;
  const closed = data.status === "closed";

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        {backLink}
        <div className="space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h1 className="min-w-0 text-2xl font-semibold tracking-tight break-words">
              <span className="mr-2 font-normal text-muted-foreground">#{data.number}</span>
              {data.subject}
            </h1>
            {closed ? null : <CloseTicketButton ticket={data} audience={audience} />}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <StatusBadge status={data.status} audience={audience} />
            {audience === "staff" ? <PriorityLabel priority={data.priority} /> : null}
            <span>
              Opened {formatDateTime(data.created_at)}
              {audience === "staff" ? ` for ${data.customer.name}` : ""}
            </span>
          </div>
        </div>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <section className="space-y-6" aria-label="Messages">
          {audience === "staff" ? <SelfServicePanel ticket={data} /> : null}
          {messages.isPending ? (
            <Skeleton className="h-32 w-full" />
          ) : messages.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(messages.error)}</AlertDescription>
            </Alert>
          ) : (
            <Conversation
              messages={messages.data.items}
              currentUserId={user.id}
              audience={audience}
            />
          )}
          {closed ? (
            <Alert>
              <LockIcon aria-hidden />
              <AlertTitle>This ticket is closed</AlertTitle>
              <AlertDescription>
                {audience === "customer" ? (
                  <span className="flex flex-wrap items-center gap-3">
                    Need more help? Open a new request.
                    <NewRequestDialog />
                  </span>
                ) : (
                  "Closed tickets can't take new replies."
                )}
              </AlertDescription>
            </Alert>
          ) : (
            <>
              {audience === "customer" && data.status === "resolved" ? (
                <Alert>
                  <CircleCheckIcon aria-hidden />
                  <AlertTitle>Marked as resolved</AlertTitle>
                  <AlertDescription>
                    Not fixed yet? Reply below and we&apos;ll pick it back up.
                  </AlertDescription>
                </Alert>
              ) : null}
              {audience === "staff" ? (
                <TicketAIPanel
                  ticket={data}
                  onUseReply={(text) => setDraft({ text, key: Date.now() })}
                />
              ) : null}
              <ReplyBox ticket={data} audience={audience} draft={draft} />
            </>
          )}
        </section>

        <aside className="space-y-4" aria-label="Ticket details">
          {audience === "staff" ? <TicketProperties ticket={data} /> : null}
          {audience === "staff" ? <TriagePanel ticket={data} /> : null}
          {audience === "staff" ? <SlaPanel ticket={data} /> : null}
          {audience === "staff" ? <RoutingPanel ticket={data} /> : null}
          {audience === "staff" ? (
            <Card size="sm">
              <CardHeader>
                <CardTitle>
                  <h2>Customer</h2>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <Link href={`/customers/${data.customer.id}`} className="font-medium hover:underline">
                  {data.customer.name}
                </Link>
                {data.customer.email ? (
                  <div className="truncate text-muted-foreground">{data.customer.email}</div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
          <Card size="sm">
            <CardHeader>
              <CardTitle>
                <h2>Activity</h2>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {events.isPending ? (
                <Skeleton className="h-16 w-full" />
              ) : events.isError ? (
                <p className="text-sm text-muted-foreground">{errorMessage(events.error)}</p>
              ) : (
                <TicketTimeline events={events.data} currentUserId={user.id} audience={audience} />
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
