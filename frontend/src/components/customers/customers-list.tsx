"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { UsersIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { NewCustomerDialog } from "@/components/customers/new-customer-dialog";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { SearchInput } from "@/components/search-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
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
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDate, initials } from "@/lib/roles";

const LIMIT = 25;

export function CustomersList() {
  const organization = useActiveOrganization();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(search.trim());
  const page = { limit: LIMIT, offset, search: debouncedSearch || undefined };

  const customers = useQuery({
    queryKey: queryKeys.customers(organization.id, page),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/customers", { params: { query: page, header: tenant(organization.id) } }),
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Customers"
        description="The people and companies you support."
        actions={<NewCustomerDialog />}
      />
      <Card>
        <CardContent className="space-y-4">
          <SearchInput
            className="max-w-sm"
            placeholder="Search by name or email"
            aria-label="Search customers"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
          {customers.isPending ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : customers.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(customers.error)}</AlertDescription>
            </Alert>
          ) : customers.data.items.length === 0 ? (
            <Empty className="py-12">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <UsersIcon aria-hidden />
                </EmptyMedia>
                <EmptyTitle>{debouncedSearch ? "No matches" : "No customers yet"}</EmptyTitle>
                <EmptyDescription>
                  {debouncedSearch
                    ? `Nobody matches “${debouncedSearch}”.`
                    : "Customers are added when you create them or when they open their first request."}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <>
              <Table aria-busy={customers.isFetching}>
                <TableHeader>
                  <TableRow>
                    <TableHead>Customer</TableHead>
                    <TableHead className="hidden sm:table-cell">Account</TableHead>
                    <TableHead className="text-right">Tickets</TableHead>
                    <TableHead className="hidden md:table-cell">Added</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {customers.data.items.map((customer) => (
                    <TableRow key={customer.id} className="relative">
                      <TableCell className="max-w-0 w-full">
                        <div className="flex items-center gap-3">
                          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                            {initials(customer.name)}
                          </span>
                          <div className="min-w-0">
                            <Link
                              href={`/customers/${customer.id}`}
                              className="block truncate font-medium after:absolute after:inset-0 hover:underline"
                            >
                              {customer.name}
                            </Link>
                            <div className="truncate text-xs text-muted-foreground">
                              {customer.email ?? "No email"}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="hidden sm:table-cell">
                        {customer.has_account ? (
                          <Badge variant="secondary">Portal access</Badge>
                        ) : (
                          <span className="text-sm text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {customer.ticket_count ?? 0}
                      </TableCell>
                      <TableCell className="hidden text-muted-foreground md:table-cell">
                        {formatDate(customer.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager
                total={customers.data.total}
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
