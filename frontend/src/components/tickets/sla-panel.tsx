"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, tenant, unwrap, type Schemas, type Ticket } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatInZone, formatMinutes } from "@/lib/sla";
import { cn } from "@/lib/utils";

type Clock = Schemas["SlaClockRead"];
type Status = Clock["status"];

// Text always says the state; color only reinforces it.
const STATUS: Record<Status, { label: string; className: string }> = {
  on_track: { label: "On track", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  at_risk: { label: "Due soon", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  breached: { label: "Breached", className: "bg-destructive/10 text-destructive dark:bg-destructive/20" },
  paused: { label: "Paused", className: "bg-secondary text-secondary-foreground" },
  met: { label: "Met", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  met_late: { label: "Met late", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  cancelled: { label: "Cancelled", className: "bg-muted text-muted-foreground" },
};

function detail(clock: Clock): string {
  const due = formatInZone(clock.due_at, clock.timezone);
  switch (clock.status) {
    case "met":
    case "met_late":
      return `Met ${formatInZone(clock.met_at!, clock.timezone)} · due ${due}`;
    case "cancelled":
      return "Closed before it was met";
    case "paused":
      return `Waiting for the customer · ${formatMinutes(Math.max(clock.remaining_minutes ?? 0, 0))} left when it resumes`;
    case "breached":
      return clock.remaining_minutes !== null && clock.remaining_minutes < 0
        ? `${formatMinutes(-clock.remaining_minutes)} overdue · was due ${due}`
        : `Was due ${due}`;
    default:
      return `${formatMinutes(clock.remaining_minutes ?? 0)} left · due ${due}`;
  }
}

function ClockRow({ clock }: { clock: Clock }) {
  const name = clock.kind === "first_response" ? "First response" : "Resolution";
  const status = STATUS[clock.status];
  return (
    <li className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {name}
          {clock.cycle > 1 ? <span className="font-normal text-muted-foreground"> (reopened)</span> : null}
        </span>
        <Badge className={cn("border-transparent", status.className)}>{status.label}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{detail(clock)}</p>
    </li>
  );
}

/** Staff: the ticket's SLA clocks (first response, resolution), in business time. */
export function SlaPanel({ ticket }: { ticket: Ticket }) {
  const organization = useActiveOrganization();
  const sla = useQuery({
    queryKey: queryKeys.ticketSla(organization.id, ticket.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/sla", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
        }),
      ),
    staleTime: 0,
    refetchInterval: 60_000, // "due soon" and "breached" follow the clock
  });
  // The latest cycle of each kind (the API lists those first).
  const current = (sla.data?.clocks ?? []).filter(
    (clock, index, all) => all.findIndex((c) => c.kind === clock.kind) === index,
  );

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2>Service level</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!sla.data ? (
          <Skeleton className="h-16 w-full" />
        ) : !sla.data.configured ? (
          <p className="text-sm text-muted-foreground">
            No SLAs yet.{" "}
            {organization.role === "admin" ? (
              <Link href="/settings/sla" className="underline-offset-4 hover:underline">
                Set up service levels
              </Link>
            ) : (
              "An admin can set up service levels."
            )}
          </p>
        ) : current.length === 0 ? (
          <p className="text-sm text-muted-foreground">No SLA policy covers this ticket&apos;s priority.</p>
        ) : (
          <ul className="space-y-3" aria-label="SLA clocks">
            {current.map((clock) => (
              <ClockRow key={`${clock.kind}-${clock.cycle}`} clock={clock} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
