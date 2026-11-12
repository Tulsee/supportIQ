import type { TicketEvent, TicketStatus } from "@/lib/api/client";
import {
  CUSTOMER_STATUS_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  formatDateTime,
  timeAgo,
} from "@/lib/tickets";

type Person = { id: string; name: string } | null;

function describe(event: TicketEvent, audience: "staff" | "customer", byMe: boolean): string {
  const labels = audience === "customer" ? CUSTOMER_STATUS_LABELS : STATUS_LABELS;
  const data = event.data as Record<string, unknown>;
  switch (event.type) {
    case "created":
      return "opened the ticket";
    case "status_changed": {
      const to = labels[data.to as TicketStatus] ?? String(data.to);
      if (data.to === "closed") {
        if (data.automatic) {
          return `closed it automatically after ${data.after_days} days without a reply`;
        }
        if (audience === "staff") return "closed the ticket";
        return byMe ? "closed this request" : "closed your request";
      }
      if (audience === "customer") {
        // In the customer's terms: someone took it on; their reply moved it along.
        if (data.to === "assigned") return "picked up your request";
        if (byMe) return `replied, moving it to ${to}`;
      }
      return `changed the status to ${to}`;
    }
    case "priority_changed":
      return `set the priority to ${PRIORITY_LABELS[data.to as keyof typeof PRIORITY_LABELS] ?? data.to}`;
    case "subject_changed":
      return `renamed the ticket to “${data.to}”`;
    case "assigned": {
      const to = data.to as Person;
      const from = data.from as Person;
      return from ? `reassigned it from ${from.name} to ${to?.name}` : `assigned it to ${to?.name}`;
    }
    case "unassigned": {
      const from = data.from as Person;
      return from ? `unassigned ${from.name}` : "unassigned the ticket";
    }
    case "team_changed": {
      const to = data.to as { name: string } | null;
      const from = data.from as { name: string } | null;
      if (!to) return from ? `took the ticket off the ${from.name} team` : "cleared the team";
      return from
        ? `moved the ticket from the ${from.name} team to ${to.name}`
        : `gave the ticket to the ${to.name} team`;
    }
    case "category_changed": {
      const to = data.to as { name: string } | null;
      const from = data.from as { name: string } | null;
      if (!to) return `removed the category ${from?.name ?? ""}`.trim();
      return from
        ? `changed the category from ${from.name} to ${to.name}`
        : `set the category to ${to.name}`;
    }
    case "sla_at_risk":
    case "sla_breached": {
      const target = data.kind === "first_response" ? "first response" : "resolution";
      return event.type === "sla_at_risk"
        ? `flagged the ${target} as due soon`
        : `recorded that the ${target} deadline passed`;
    }
    case "ai_escalated":
      return data.reason === "conflicting_sources"
        ? "asked the AI for a reply; the documents disagree, so a person should answer"
        : "asked the AI for a reply; it couldn't find a supported answer";
  }
}

/** Status, priority and assignment history, oldest first. */
export function TicketTimeline({
  events,
  currentUserId,
  audience,
}: {
  events: TicketEvent[];
  currentUserId: string;
  audience: "staff" | "customer";
}) {
  return (
    <ol className="relative space-y-3 border-l pl-4 text-sm">
      {events.map((event) => {
        const byMe = event.actor?.id === currentUserId;
        // No actor: an automatic change (SupportIQ), or an account deleted since.
        const automatic = (event.data as Record<string, unknown>).automatic === true;
        const actor = byMe
          ? "You"
          : (event.actor?.full_name ?? (automatic ? "SupportIQ" : "Someone"));
        return (
          <li key={event.id} className="relative">
            <span
              className="absolute top-1.5 -left-[21px] size-2 rounded-full bg-muted-foreground/40 ring-4 ring-card"
              aria-hidden
            />
            <span className="font-medium">{actor}</span> {describe(event, audience, byMe)}
            <div className="text-xs text-muted-foreground">
              <time dateTime={event.created_at} title={formatDateTime(event.created_at)}>
                {timeAgo(event.created_at)}
              </time>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
