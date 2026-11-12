"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon, Loader2Icon, RouteIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { DecisionDetails, OutcomeBadge } from "@/components/routing/decision-details";
import { refreshTickets } from "@/components/tickets/hooks";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage, tenant, unwrap, type RoutingDecision, type Ticket } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

function how(decision: RoutingDecision): string {
  switch (decision.trigger) {
    case "created":
      return "When it was opened";
    case "sweep":
      return "Shortly after it was opened";
    case "reroute":
      return decision.actor ? `Rerouted by ${decision.actor.full_name}` : "Rerouted";
  }
}

function Decision({ decision, open }: { decision: RoutingDecision; open?: boolean }) {
  const [expanded, setExpanded] = useState(open ?? false);
  const hasDetails = decision.rules.length > 0 || decision.candidates.length > 0;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <OutcomeBadge outcome={decision.outcome} />
        {decision.applied ? null : (
          <span className="text-xs text-muted-foreground">Shadow: not carried out</span>
        )}
      </div>
      <p className="text-sm">{decision.explanation}</p>
      <p className="text-xs text-muted-foreground">
        {how(decision)} ·{" "}
        <time dateTime={decision.created_at} title={formatDateTime(decision.created_at)}>
          {timeAgo(decision.created_at)}
        </time>
        {decision.rule?.name && decision.rule_version ? ` · rule version ${decision.rule_version}` : ""}
      </p>
      {hasDetails ? (
        <>
          <Button
            variant="link"
            size="xs"
            className="h-auto p-0"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronDownIcon className={expanded ? "rotate-180" : ""} aria-hidden />
            {expanded ? "Hide details" : "Rules and agents"}
          </Button>
          {expanded ? (
            <DecisionDetails
              rules={decision.rules}
              candidates={decision.candidates}
              showSkills={decision.strategy === "skill_match"}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/** Staff: how routing handled the ticket, and a way to run it again. */
export function RoutingPanel({ ticket }: { ticket: Ticket }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const [showEarlier, setShowEarlier] = useState(false);
  const params = { path: { ticket_id: ticket.id }, header: tenant(organization.id) };
  const decisions = useQuery({
    queryKey: queryKeys.ticketRouting(organization.id, ticket.id),
    queryFn: () => unwrap(api.GET("/api/v1/tickets/{ticket_id}/routing", { params })),
    staleTime: 0,
  });
  const reroute = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/tickets/{ticket_id}/reroute", { params })),
    onSuccess: async (decision) => {
      toast.success(decision.outcome === "assigned" ? `Assigned to ${decision.assignee?.name}` : "Routed");
      await refreshTickets(queryClient, organization.id);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const routable = !ticket.assignee && ticket.status !== "resolved" && ticket.status !== "closed";
  const [latest, ...earlier] = decisions.data ?? [];

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2>Routing</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {decisions.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : decisions.isError ? (
          <p className="text-sm text-muted-foreground">{errorMessage(decisions.error)}</p>
        ) : latest ? (
          <Decision key={latest.id} decision={latest} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Routing hasn&apos;t run on this ticket.
            {organization.role === "admin" ? (
              <>
                {" "}
                <Link href="/settings/routing" className="underline-offset-4 hover:underline">
                  Routing settings
                </Link>
              </>
            ) : null}
          </p>
        )}
        {routable ? (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            disabled={reroute.isPending}
            onClick={() => reroute.mutate()}
          >
            {reroute.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <RouteIcon aria-hidden />}
            {latest ? "Reroute" : "Route now"}
          </Button>
        ) : null}
        {earlier.length ? (
          <div className="space-y-3 border-t pt-3">
            <Button
              variant="link"
              size="xs"
              className="h-auto p-0"
              aria-expanded={showEarlier}
              onClick={() => setShowEarlier(!showEarlier)}
            >
              <ChevronDownIcon className={showEarlier ? "rotate-180" : ""} aria-hidden />
              {earlier.length === 1 ? "1 earlier decision" : `${earlier.length} earlier decisions`}
            </Button>
            {showEarlier
              ? earlier.map((decision) => <Decision key={decision.id} decision={decision} />)
              : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
