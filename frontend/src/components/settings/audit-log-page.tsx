"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { AUDIT_ENTITY_LABELS, type AuditEntityType } from "@/lib/notifications";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 25;
type Filter = "all" | AuditEntityType;
const FILTERS: Filter[] = [
  "all",
  "organization",
  "member",
  "invitation",
  "document",
  "category",
  "sla",
  "team",
  "agent",
];

/** Administrative changes in the organization, newest first. */
export function AuditLogPage() {
  const organization = useActiveOrganization();
  const [filter, setFilter] = useState<Filter>("all");
  const [offset, setOffset] = useState(0);
  const entityType = filter === "all" ? undefined : filter;

  const log = useQuery({
    queryKey: queryKeys.auditLog(organization.id, { entityType, offset }),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/audit-log", {
          params: {
            header: tenant(organization.id),
            query: { entity_type: entityType, limit: LIMIT, offset },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Changes to the organization, its members and its documents: who did what, and when. Entries can't be edited or deleted."
      />
      <Tabs
        value={filter}
        onValueChange={(value) => {
          setFilter(value as Filter);
          setOffset(0);
        }}
        className="mb-4"
      >
        <TabsList aria-label="Filter by what changed">
          {FILTERS.map((value) => (
            <TabsTrigger key={value} value={value}>
              {value === "all" ? "All" : AUDIT_ENTITY_LABELS[value]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {log.error ? (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{errorMessage(log.error)}</AlertDescription>
        </Alert>
      ) : null}
      <Card>
        <CardContent>
          {!log.data ? (
            <Skeleton className="h-40 w-full" />
          ) : log.data.items.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Nothing recorded yet.</p>
          ) : (
            <Table aria-busy={log.isFetching}>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">When</TableHead>
                  <TableHead className="w-44">Who</TableHead>
                  <TableHead>What</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {log.data.items.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="align-top whitespace-nowrap text-muted-foreground">
                      <time dateTime={entry.created_at} title={formatDateTime(entry.created_at)}>
                        {timeAgo(entry.created_at)}
                      </time>
                    </TableCell>
                    <TableCell className="align-top">{entry.actor_name ?? "Deleted user"}</TableCell>
                    <TableCell className="align-top whitespace-normal">
                      <div className="flex flex-wrap items-center gap-2">
                        <span>{entry.summary}</span>
                        <Badge variant="outline" className="font-mono text-[11px]">
                          {entry.action}
                        </Badge>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {log.data ? (
        <div className="mt-4">
          <Pager total={log.data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
        </div>
      ) : null}
    </>
  );
}
