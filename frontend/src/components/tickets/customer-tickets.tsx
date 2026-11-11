"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRightIcon, LifeBuoyIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { NewRequestDialog } from "@/components/tickets/new-request-dialog";
import { StatusBadge } from "@/components/tickets/ticket-badges";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { LIVE_QUERY } from "@/components/tickets/hooks";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 20;

/** Customers: their own requests (the API returns nothing else). */
export function CustomerTickets({ limit = LIMIT, compact = false }: { limit?: number; compact?: boolean }) {
  const organization = useActiveOrganization();
  const [offset, setOffset] = useState(0);
  const query = { limit, offset };
  const tickets = useQuery({
    queryKey: queryKeys.tickets(organization.id, query),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets", { params: { query, header: tenant(organization.id) } })),
    placeholderData: keepPreviousData,
  });

  const list = tickets.isPending ? (
    <div className="space-y-2">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  ) : tickets.isError ? (
    <Alert variant="destructive">
      <AlertDescription>{errorMessage(tickets.error)}</AlertDescription>
    </Alert>
  ) : tickets.data.items.length === 0 ? (
    <Card>
      <Empty className="py-12">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <LifeBuoyIcon aria-hidden />
          </EmptyMedia>
          <EmptyTitle>No requests yet</EmptyTitle>
          <EmptyDescription>
            Ask {organization.name} a question or report a problem, and follow the replies here.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <NewRequestDialog />
        </EmptyContent>
      </Empty>
    </Card>
  ) : (
    <>
      <Card className="gap-0 divide-y py-0">
        {tickets.data.items.map((ticket) => (
          <Link
            key={ticket.id}
            href={`/tickets/${ticket.id}`}
            className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-muted/50"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{ticket.subject}</div>
              <div className="text-xs text-muted-foreground">
                #{ticket.number} · updated{" "}
                <time dateTime={ticket.updated_at} title={formatDateTime(ticket.updated_at)}>
                  {timeAgo(ticket.updated_at)}
                </time>
              </div>
            </div>
            <StatusBadge status={ticket.status} audience="customer" />
            <ChevronRightIcon className="size-4 text-muted-foreground" aria-hidden />
          </Link>
        ))}
      </Card>
      {compact ? null : (
        <Pager total={tickets.data.total} limit={limit} offset={offset} onOffsetChange={setOffset} />
      )}
    </>
  );

  if (compact) return list;
  return (
    <>
      <PageHeader
        title="My requests"
        description={`Your conversations with ${organization.name}.`}
        actions={<NewRequestDialog />}
      />
      {list}
    </>
  );
}
