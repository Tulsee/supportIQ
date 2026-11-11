"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { SearchInput } from "@/components/search-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { queryKeys } from "@/lib/query-keys";
import { formatDate, initials } from "@/lib/roles";

const LIMIT = 20;

/**
 * Every organization on the platform. Read-only: platform admins manage accounts,
 * not tenant data, and get no access to an organization's contents from here.
 */
export function AdminOrganizations() {
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(search.trim());
  const page = { limit: LIMIT, offset, search: debouncedSearch || undefined };

  const organizations = useQuery({
    queryKey: queryKeys.adminOrganizations(page),
    queryFn: () => unwrap(api.GET("/api/v1/admin/organizations", { params: { query: page } })),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="All organizations"
        description="Every organization on the platform. Their data stays private to their members."
      />
      <Card>
        <CardContent className="space-y-4">
          <SearchInput
            className="max-w-sm"
            placeholder="Search by name or slug"
            aria-label="Search organizations"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
          {organizations.isPending ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : organizations.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(organizations.error)}</AlertDescription>
            </Alert>
          ) : organizations.data.items.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {debouncedSearch
                ? `No organizations match “${debouncedSearch}”.`
                : "No organizations yet."}
            </p>
          ) : (
            <>
              <Table aria-busy={organizations.isFetching}>
                <TableHeader>
                  <TableRow>
                    <TableHead>Organization</TableHead>
                    <TableHead className="text-right">Members</TableHead>
                    <TableHead className="hidden md:table-cell">Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {organizations.data.items.map((org) => (
                    <TableRow key={org.id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-xs font-semibold">
                            {initials(org.name)}
                          </span>
                          <div className="min-w-0">
                            <div className="truncate font-medium">{org.name}</div>
                            <div className="truncate font-mono text-xs text-muted-foreground">
                              {org.slug}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{org.member_count}</TableCell>
                      <TableCell className="hidden text-muted-foreground md:table-cell">
                        {formatDate(org.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager
                total={organizations.data.total}
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
