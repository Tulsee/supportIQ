"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ScanSearchIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { QUERY_TYPE_LABELS, STRATEGY_LABELS, formatMs } from "@/lib/retrieval";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 25;

/** Every retrieval run in the organization, newest first. */
export function RunsList() {
  const organization = useActiveOrganization();
  const [offset, setOffset] = useState(0);
  const page = { limit: LIMIT, offset };

  const runs = useQuery({
    queryKey: queryKeys.aiRuns(organization.id, page),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/ai/runs", { params: { query: page, header: tenant(organization.id) } }),
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="AI inspector"
        description="Every retrieval run: what was asked, how it was searched, and what came back."
      />
      <Card>
        <CardContent className="space-y-4">
          {runs.isPending ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : runs.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(runs.error)}</AlertDescription>
            </Alert>
          ) : runs.data.items.length === 0 ? (
            <Empty className="py-12">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <ScanSearchIcon aria-hidden />
                </EmptyMedia>
                <EmptyTitle>No runs yet</EmptyTitle>
                <EmptyDescription>
                  Runs appear here when someone tests retrieval in the knowledge base.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Link href="/knowledge-base" className="text-sm font-medium hover:underline">
                  Go to the knowledge base
                </Link>
              </EmptyContent>
            </Empty>
          ) : (
            <>
              <Table aria-busy={runs.isFetching}>
                <TableHeader>
                  <TableRow>
                    <TableHead>Query</TableHead>
                    <TableHead className="hidden md:table-cell">Strategy</TableHead>
                    <TableHead className="text-right">Results</TableHead>
                    <TableHead className="hidden text-right sm:table-cell">Latency</TableHead>
                    <TableHead className="hidden lg:table-cell">By</TableHead>
                    <TableHead className="hidden sm:table-cell">When</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.items.map((run) => (
                    <TableRow key={run.id} className="relative">
                      <TableCell className="max-w-0 w-full">
                        <Link
                          href={`/ai-inspector/${run.id}`}
                          className="block truncate font-medium after:absolute after:inset-0 hover:underline"
                        >
                          {run.query}
                        </Link>
                        <div className="truncate text-xs text-muted-foreground">
                          {QUERY_TYPE_LABELS[run.query_type] ?? run.query_type}
                        </div>
                      </TableCell>
                      <TableCell className="hidden md:table-cell">
                        <Badge variant="secondary" className="whitespace-nowrap">
                          {STRATEGY_LABELS[run.strategy] ?? run.strategy}
                        </Badge>
                        {run.requested_strategy !== "auto" ? (
                          <span className="ml-1.5 text-xs text-muted-foreground">fixed</span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{run.result_count}</TableCell>
                      <TableCell className="hidden text-right whitespace-nowrap text-muted-foreground tabular-nums sm:table-cell">
                        {formatMs(run.latency_ms)}
                      </TableCell>
                      <TableCell className="hidden whitespace-nowrap text-muted-foreground lg:table-cell">
                        {run.user?.full_name ?? "Deleted user"}
                      </TableCell>
                      <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">
                        <time dateTime={run.created_at} title={formatDateTime(run.created_at)}>
                          {timeAgo(run.created_at)}
                        </time>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager
                total={runs.data.total}
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
