"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  CircleAlertIcon,
  DownloadIcon,
  FileUpIcon,
  RefreshCwIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { DocumentStatusBadge } from "@/components/knowledge/document-status";
import { UploadDocumentDialog } from "@/components/knowledge/upload-document-dialog";
import { Pager } from "@/components/pager";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  api,
  errorMessage,
  tenant,
  unwrap,
  type DocumentChunk,
  type KnowledgeDocument,
} from "@/lib/api/client";
import { FILE_TYPE_LABELS, formatBytes, isIndexing, pageLabel } from "@/lib/documents";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime } from "@/lib/tickets";

const CHUNK_LIMIT = 20;

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function ChunkCard({ chunk }: { chunk: DocumentChunk }) {
  const [expanded, setExpanded] = useState(false);
  const page = pageLabel(chunk);
  return (
    <li className="rounded-lg border p-3">
      <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Chunk {chunk.chunk_index + 1}</span>
        {page ? <span>{page}</span> : null}
        {chunk.section_heading ? <span>§ {chunk.section_heading}</span> : null}
        <span className="tabular-nums">{chunk.token_count} tokens</span>
        <span className="tabular-nums">
          chars {chunk.start_offset}–{chunk.end_offset}
        </span>
      </div>
      <p className={`text-sm whitespace-pre-wrap ${expanded ? "" : "line-clamp-3"}`}>
        {chunk.content}
      </p>
      <Button
        variant="link"
        size="xs"
        className="h-auto p-0"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {expanded ? "Show less" : "Show all"}
      </Button>
    </li>
  );
}

function Chunks({ document }: { document: KnowledgeDocument }) {
  const organization = useActiveOrganization();
  const [offset, setOffset] = useState(0);
  // indexed_at is part of the key: every completed (re-)index shows its own chunks,
  // instead of the empty list fetched while indexing was still running.
  const page = { limit: CHUNK_LIMIT, offset };
  const chunks = useQuery({
    queryKey: [...queryKeys.documentChunks(organization.id, document.id, page), document.indexed_at],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents/{document_id}/chunks", {
          params: {
            path: { document_id: document.id },
            query: page,
            header: tenant(organization.id),
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Indexed chunks</h2>
        </CardTitle>
        <CardDescription>
          The passages search and answers draw from
          {document.indexed_version ? `, from version ${document.indexed_version}` : ""}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {chunks.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : chunks.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(chunks.error)}</AlertDescription>
          </Alert>
        ) : chunks.data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {isIndexing(document) ? "Indexing… chunks appear here when it finishes." : "No chunks."}
          </p>
        ) : (
          <>
            <ol className="space-y-3">
              {chunks.data.items.map((chunk) => (
                <ChunkCard key={chunk.id} chunk={chunk} />
              ))}
            </ol>
            <Pager
              total={chunks.data.total}
              limit={CHUNK_LIMIT}
              offset={offset}
              onOffsetChange={setOffset}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const isAdmin = organization.role === "admin";
  const header = tenant(organization.id);
  // Once deleted, stop querying it: visited pages stay mounted in the background,
  // and a refresh would otherwise ask for a document that no longer exists.
  const [deleted, setDeleted] = useState(false);

  const document = useQuery({
    queryKey: queryKeys.document(organization.id, id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/documents/{document_id}", { params: { path: { document_id: id }, header } })),
    enabled: !deleted,
    staleTime: 0,
    refetchInterval: (current) => (current.state.data && isIndexing(current.state.data) ? 2_000 : false),
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.documents(organization.id), refetchType: "all" });

  const reindex = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/documents/{document_id}/reindex", { params: { path: { document_id: id }, header } })),
    onSuccess: async () => {
      toast.success("Indexing again");
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const download = useMutation({
    mutationFn: async (doc: KnowledgeDocument) => {
      const { data, response } = await api.GET("/api/v1/documents/{document_id}/file", {
        params: { path: { document_id: doc.id }, header },
        parseAs: "blob",
      });
      if (!response.ok || !data) throw new ApiError(response.status, "download_failed", "Download failed");
      const url = URL.createObjectURL(data as Blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = doc.filename;
      link.click();
      URL.revokeObjectURL(url);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const remove = async () => {
    try {
      await unwrap(api.DELETE("/api/v1/documents/{document_id}", { params: { path: { document_id: id }, header } }));
      setDeleted(true);
      queryClient.removeQueries({ queryKey: queryKeys.document(organization.id, id) });
      toast.success("Document deleted");
      router.replace("/knowledge-base");
      await refresh();
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  const backLink = (
    <Link
      href="/knowledge-base"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeftIcon className="size-4" aria-hidden />
      Knowledge base
    </Link>
  );

  if (deleted) {
    return (
      <div className="space-y-4">
        {backLink}
        <p className="text-sm text-muted-foreground">This document was deleted.</p>
      </div>
    );
  }
  if (document.isPending) {
    return (
      <div className="space-y-4">
        {backLink}
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (document.isError) {
    const notFound = document.error instanceof ApiError && document.error.status === 404;
    return (
      <div className="space-y-4">
        {backLink}
        <Alert variant={notFound ? "default" : "destructive"}>
          <AlertTitle>{notFound ? "Document not found" : "Couldn't load this document"}</AlertTitle>
          <AlertDescription>
            {notFound ? "It may have been deleted." : errorMessage(document.error)}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const doc = document.data;
  const indexing = isIndexing(doc);
  const newerVersionPending = doc.indexed_version !== null && doc.indexed_version < doc.version;

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        {backLink}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight break-words">{doc.title}</h1>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <DocumentStatusBadge status={doc.status} />
              <Badge variant="outline">{FILE_TYPE_LABELS[doc.file_type]}</Badge>
              <span className="truncate">{doc.filename}</span>
              <span>· version {doc.version}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => download.mutate(doc)} disabled={download.isPending}>
              <DownloadIcon aria-hidden />
              Download
            </Button>
            {isAdmin ? (
              <>
                <UploadDocumentDialog
                  document={doc}
                  trigger={
                    <Button variant="outline" disabled={indexing}>
                      <FileUpIcon aria-hidden />
                      New version
                    </Button>
                  }
                />
                <Button
                  variant="outline"
                  onClick={() => reindex.mutate()}
                  disabled={indexing || reindex.isPending}
                >
                  <RefreshCwIcon aria-hidden />
                  Re-index
                </Button>
                <ConfirmAction
                  triggerProps={{ variant: "outline" }}
                  title={`Delete “${doc.title}”?`}
                  description="Its file, chunks, and search index entries are removed. Answers can no longer cite it."
                  confirmLabel="Delete"
                  onConfirm={remove}
                >
                  <Trash2Icon aria-hidden />
                  Delete
                </ConfirmAction>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {doc.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlertIcon aria-hidden />
          <AlertTitle>Indexing failed</AlertTitle>
          <AlertDescription>
            {doc.error}
            {doc.indexed_version ? ` Version ${doc.indexed_version} is still searchable.` : ""}
            {isAdmin ? " Fix the file and upload a new version, or re-index to try again." : ""}
          </AlertDescription>
        </Alert>
      ) : doc.status === "indexed" && doc.error ? (
        <Alert>
          <TriangleAlertIcon aria-hidden />
          <AlertTitle>Indexed with warnings</AlertTitle>
          <AlertDescription className="whitespace-pre-line">{doc.error}</AlertDescription>
        </Alert>
      ) : indexing ? (
        <Alert>
          <RefreshCwIcon className="animate-spin" aria-hidden />
          <AlertTitle>{doc.status === "queued" ? "Waiting for a worker" : "Indexing"}</AlertTitle>
          <AlertDescription>
            {newerVersionPending
              ? `Version ${doc.version} is being indexed; version ${doc.indexed_version} stays searchable until it's done.`
              : "Extracting text, chunking, and embedding. This page updates when it's done."}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <Chunks document={doc} />
        <Card size="sm">
          <CardHeader>
            <CardTitle>
              <h2>Details</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3">
              <Detail label="Size">{formatBytes(doc.size_bytes)}</Detail>
              {doc.page_count !== null ? <Detail label="Pages">{doc.page_count}</Detail> : null}
              <Detail label="Chunks">
                {doc.chunk_count} ({doc.token_count.toLocaleString()} tokens)
              </Detail>
              <Detail label="Searchable version">{doc.indexed_version ?? "None yet"}</Detail>
              {doc.embedding_model ? (
                <Detail label="Embedding model">
                  <span className="font-mono text-xs">{doc.embedding_model}</span>
                </Detail>
              ) : null}
              <Detail label="Uploaded">
                {formatDateTime(doc.created_at)}
                {doc.uploaded_by ? ` by ${doc.uploaded_by.full_name}` : ""}
              </Detail>
              {doc.indexed_at ? <Detail label="Indexed">{formatDateTime(doc.indexed_at)}</Detail> : null}
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
