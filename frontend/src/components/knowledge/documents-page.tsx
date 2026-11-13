"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BookOpenIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DocumentStatusBadge, PublicBadge } from "@/components/knowledge/document-status";
import { UploadDocumentDialog } from "@/components/knowledge/upload-document-dialog";
import { NoWorkerAlert, useWorkerOffline } from "@/components/knowledge/worker-status";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { RetrievalPlayground } from "@/components/retrieval/retrieval-playground";
import { SearchInput } from "@/components/search-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
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
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import {
  FILE_TYPE_LABELS,
  VISIBILITY_FILTER_LABELS,
  formatBytes,
  isIndexing,
  type Visibility,
} from "@/lib/documents";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 25;
const POLL_MS = 2_000;
const VISIBILITY_FILTERS = Object.keys(VISIBILITY_FILTER_LABELS) as (Visibility | "all")[];

export function DocumentsPage() {
  const organization = useActiveOrganization();
  const isAdmin = organization.role === "admin";
  const [search, setSearch] = useState("");
  const [visibility, setVisibility] = useState<Visibility | "all">("all");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(search.trim());
  const query = {
    limit: LIMIT,
    offset,
    search: debouncedSearch || undefined,
    visibility: visibility === "all" ? undefined : visibility,
  };
  const filtered = Boolean(debouncedSearch) || visibility !== "all";

  const documents = useQuery({
    queryKey: queryKeys.documents(organization.id, query),
    queryFn: () =>
      unwrap(api.GET("/api/v1/documents", { params: { query, header: tenant(organization.id) } })),
    placeholderData: keepPreviousData,
    staleTime: 0,
    // Indexing runs in the background: follow it until every document settles.
    refetchInterval: (current) =>
      current.state.data?.items.some(isIndexing) ? POLL_MS : false,
  });
  const anyQueued = documents.data?.items.some((d) => d.status === "queued") ?? false;
  const workerOffline = useWorkerOffline(anyQueued);

  return (
    <>
      <PageHeader
        title="Knowledge base"
        description="Documents the AI assistant answers from, with citations back to the source."
        actions={isAdmin ? <UploadDocumentDialog /> : null}
      />
      <div className="space-y-6">
        {workerOffline ? <NoWorkerAlert /> : null}
        <Card>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <SearchInput
                className="max-w-sm"
                placeholder="Filter by title or file name"
                aria-label="Filter documents"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
              />
              <Select
                items={VISIBILITY_FILTER_LABELS}
                value={visibility}
                onValueChange={(value) => {
                  if (!value) return;
                  setVisibility(value as Visibility | "all");
                  setOffset(0);
                }}
              >
                <SelectTrigger aria-label="Visibility" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {VISIBILITY_FILTERS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {VISIBILITY_FILTER_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {documents.isPending ? (
              <div className="space-y-2">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : documents.isError ? (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(documents.error)}</AlertDescription>
              </Alert>
            ) : documents.data.items.length === 0 ? (
              <Empty className="py-12">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <BookOpenIcon aria-hidden />
                  </EmptyMedia>
                  <EmptyTitle>{filtered ? "No matches" : "No documents yet"}</EmptyTitle>
                  <EmptyDescription>
                    {debouncedSearch
                      ? `No ${visibility === "all" ? "" : `${visibility} `}document matches “${debouncedSearch}”.`
                      : visibility === "public"
                        ? "No document is public yet. Open one and turn on “Answers customers” to let the customer assistant use it."
                        : visibility === "internal"
                          ? "Every document is public."
                          : "Upload manuals, policies, and FAQs so answers can cite them."}
                  </EmptyDescription>
                </EmptyHeader>
                {isAdmin && !filtered ? (
                  <EmptyContent>
                    <UploadDocumentDialog />
                  </EmptyContent>
                ) : null}
              </Empty>
            ) : (
              <>
                <Table aria-busy={documents.isFetching}>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Document</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="hidden text-right md:table-cell">Chunks</TableHead>
                      <TableHead className="hidden lg:table-cell">Size</TableHead>
                      <TableHead className="hidden sm:table-cell">Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {documents.data.items.map((document) => (
                      <TableRow key={document.id} className="relative">
                        <TableCell className="w-full max-w-0">
                          <Link
                            href={`/knowledge-base/${document.id}`}
                            className="block truncate font-medium after:absolute after:inset-0 hover:underline"
                          >
                            {document.title}
                          </Link>
                          <div className="flex items-center gap-2 truncate text-xs text-muted-foreground">
                            <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                              {FILE_TYPE_LABELS[document.file_type]}
                            </Badge>
                            <span className="truncate">{document.filename}</span>
                            {document.version > 1 ? <span>v{document.version}</span> : null}
                            {document.visibility === "public" ? <PublicBadge /> : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          <DocumentStatusBadge status={document.status} />
                        </TableCell>
                        <TableCell className="hidden text-right tabular-nums md:table-cell">
                          {document.status === "indexed" || document.indexed_version
                            ? document.chunk_count
                            : "—"}
                        </TableCell>
                        <TableCell className="hidden text-sm whitespace-nowrap text-muted-foreground lg:table-cell">
                          {formatBytes(document.size_bytes)}
                        </TableCell>
                        <TableCell className="hidden text-sm whitespace-nowrap text-muted-foreground sm:table-cell">
                          <time
                            dateTime={document.updated_at}
                            title={formatDateTime(document.updated_at)}
                          >
                            {timeAgo(document.updated_at)}
                          </time>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <Pager
                  total={documents.data.total}
                  limit={LIMIT}
                  offset={offset}
                  onOffsetChange={setOffset}
                />
              </>
            )}
          </CardContent>
        </Card>
        <RetrievalPlayground />
      </div>
    </>
  );
}
