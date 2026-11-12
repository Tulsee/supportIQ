"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { InboxIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { SearchInput } from "@/components/search-input";
import { NewTicketDialog } from "@/components/tickets/new-ticket-dialog";
import { PriorityLabel, StatusBadge } from "@/components/tickets/ticket-badges";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LIVE_QUERY, useCategories } from "@/components/tickets/hooks";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api, errorMessage, tenant, unwrap, type TicketPriority, type TicketStatus } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { initials } from "@/lib/roles";
import { ACTIVE_STATUSES, PRIORITIES, PRIORITY_LABELS, formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 25;

type View = "active" | "mine" | "unassigned" | "resolved" | "closed" | "all";
const VIEWS: { value: View; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "mine", label: "Mine" },
  { value: "unassigned", label: "Unassigned" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

function viewFilters(view: View): { status?: TicketStatus[]; assignee?: "me" | "unassigned" } {
  switch (view) {
    case "active":
      return { status: ACTIVE_STATUSES };
    case "mine":
      return { status: ACTIVE_STATUSES, assignee: "me" };
    case "unassigned":
      return { status: ACTIVE_STATUSES, assignee: "unassigned" };
    case "resolved":
      return { status: ["resolved"] };
    case "closed":
      return { status: ["closed"] };
    case "all":
      return {};
  }
}

type Sort = "-updated_at" | "-created_at" | "created_at" | "-priority";
const SORT_LABELS: Record<Sort, string> = {
  "-updated_at": "Recent activity",
  "-created_at": "Newest first",
  created_at: "Oldest first",
  "-priority": "Priority",
};
const ANY_PRIORITY = "any";
const ANY_CATEGORY = "any";
const PRIORITY_FILTER_LABELS: Record<string, string> = {
  [ANY_PRIORITY]: "Any priority",
  ...PRIORITY_LABELS,
};

/** Admins and agents: every ticket of the organization. */
export function TicketsInbox() {
  const organization = useActiveOrganization();
  const [view, setView] = useState<View>("active");
  const [search, setSearch] = useState("");
  const [priority, setPriority] = useState<string>(ANY_PRIORITY);
  const [category, setCategory] = useState<string>(ANY_CATEGORY);
  const { categories } = useCategories(true);
  const categoryLabels: Record<string, string> = {
    [ANY_CATEGORY]: "Any category",
    ...Object.fromEntries(categories.map((c) => [c.id, c.name])),
  };
  const [sort, setSort] = useState<Sort>("-updated_at");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(search.trim());

  const query = {
    ...viewFilters(view),
    priority: priority === ANY_PRIORITY ? undefined : [priority as TicketPriority],
    category_id: category === ANY_CATEGORY ? undefined : category,
    search: debouncedSearch || undefined,
    sort,
    limit: LIMIT,
    offset,
  };
  const tickets = useQuery({
    queryKey: queryKeys.tickets(organization.id, query),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets", { params: { query, header: tenant(organization.id) } })),
    placeholderData: keepPreviousData,
  });
  const counts = useQuery({
    queryKey: queryKeys.ticketCounts(organization.id),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/counts", { params: { header: tenant(organization.id) } }),
      ),
  });

  const viewCount = (value: View): number | undefined => {
    const data = counts.data;
    if (!data) return undefined;
    const active = ACTIVE_STATUSES.reduce((sum, s) => sum + data.by_status[s], 0);
    return {
      active,
      mine: data.assigned_to_me,
      unassigned: data.unassigned,
      resolved: data.by_status.resolved,
      closed: data.by_status.closed,
      all: Object.values(data.by_status).reduce((sum, n) => sum + n, 0),
    }[value];
  };

  const resetPage = <T,>(set: (value: T) => void) => (value: T) => {
    set(value);
    setOffset(0);
  };

  return (
    <>
      <PageHeader
        title="Tickets"
        description={`Customer requests in ${organization.name}.`}
        actions={<NewTicketDialog />}
      />
      <Card>
        <CardContent className="space-y-4">
          <Tabs value={view} onValueChange={(value) => resetPage(setView)(value as View)}>
            <TabsList variant="line" className="flex-wrap">
              {VIEWS.map(({ value, label }) => {
                const count = viewCount(value);
                return (
                  <TabsTrigger key={value} value={value}>
                    {label}
                    {count !== undefined ? (
                      <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums text-muted-foreground">
                        {count}
                      </span>
                    ) : null}
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput
              className="min-w-56 flex-1 sm:max-w-sm"
              placeholder="Search by #number, customer, or words"
              aria-label="Search tickets"
              value={search}
              onChange={(event) => resetPage(setSearch)(event.target.value)}
            />
            <Select
              items={PRIORITY_FILTER_LABELS}
              value={priority}
              onValueChange={(value) => value && resetPage(setPriority)(value)}
            >
              <SelectTrigger aria-label="Filter by priority" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[ANY_PRIORITY, ...PRIORITIES].map((value) => (
                  <SelectItem key={value} value={value}>
                    {PRIORITY_FILTER_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {categories.length ? (
              <Select
                items={categoryLabels}
                value={category}
                onValueChange={(value) => value && resetPage(setCategory)(value)}
              >
                <SelectTrigger aria-label="Filter by category" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(categoryLabels).map(([id, name]) => (
                    <SelectItem key={id} value={id}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            <Select
              items={SORT_LABELS}
              value={sort}
              onValueChange={(value) => value && resetPage(setSort)(value as Sort)}
            >
              <SelectTrigger aria-label="Sort tickets" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(SORT_LABELS) as Sort[]).map((value) => (
                  <SelectItem key={value} value={value}>
                    {SORT_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {tickets.isPending ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : tickets.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(tickets.error)}</AlertDescription>
            </Alert>
          ) : tickets.data.items.length === 0 ? (
            <Empty className="py-12">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <InboxIcon aria-hidden />
                </EmptyMedia>
                <EmptyTitle>No tickets here</EmptyTitle>
                <EmptyDescription>
                  {debouncedSearch
                    ? `Nothing matches “${debouncedSearch}”.`
                    : view === "active" || view === "all"
                      ? "New requests from customers will show up here."
                      : "Try another view."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <>
              <Table aria-busy={tickets.isFetching}>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-16">#</TableHead>
                    <TableHead>Subject</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="hidden md:table-cell">Priority</TableHead>
                    <TableHead className="hidden lg:table-cell">Assignee</TableHead>
                    <TableHead className="hidden sm:table-cell">Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tickets.data.items.map((ticket) => (
                    <TableRow key={ticket.id} className="relative">
                      <TableCell className="text-muted-foreground tabular-nums">
                        {ticket.number}
                      </TableCell>
                      <TableCell className="max-w-0 w-full">
                        {/* The link covers the row, so the whole row is clickable. */}
                        <Link
                          href={`/tickets/${ticket.id}`}
                          className="block truncate font-medium after:absolute after:inset-0 hover:underline"
                        >
                          {ticket.subject}
                        </Link>
                        <div className="truncate text-xs text-muted-foreground">
                          {ticket.customer.name}
                          {ticket.category ? ` · ${ticket.category.name}` : ""}
                        </div>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={ticket.status} />
                      </TableCell>
                      <TableCell className="hidden md:table-cell">
                        <PriorityLabel priority={ticket.priority} />
                      </TableCell>
                      <TableCell className="hidden lg:table-cell">
                        {ticket.assignee ? (
                          <span className="flex items-center gap-2">
                            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold">
                              {initials(ticket.assignee.full_name)}
                            </span>
                            <span className="truncate text-sm">{ticket.assignee.full_name}</span>
                          </span>
                        ) : (
                          <span className="text-sm text-muted-foreground">Unassigned</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden text-sm whitespace-nowrap text-muted-foreground sm:table-cell">
                        <time dateTime={ticket.updated_at} title={formatDateTime(ticket.updated_at)}>
                          {timeAgo(ticket.updated_at)}
                        </time>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager
                total={tickets.data.total}
                limit={LIMIT}
                offset={offset}
                onOffsetChange={setOffset}
              />
            </>
          )}
        </CardContent>
      </Card>
    </>
  );
}
