"use client";

import { useQuery } from "@tanstack/react-query";
import { SearchIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { pageLabel } from "@/lib/documents";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

/** Try the knowledge base the way the AI assistant will: semantic search. */
export function KnowledgeSearch() {
  const organization = useActiveOrganization();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");

  const results = useQuery({
    queryKey: queryKeys.knowledgeSearch(organization.id, query),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents/search", {
          params: { query: { q: query, limit: 8 }, header: tenant(organization.id) },
        }),
      ),
    enabled: query.length >= 2,
    staleTime: 0,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Search the knowledge base</h2>
        </CardTitle>
        <CardDescription>
          Finds passages by meaning, not only by exact words. This is what the AI assistant will
          draw its answers from.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          role="search"
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
          }}
        >
          <Input
            aria-label="Search question"
            placeholder="e.g. How long do refunds take?"
            value={draft}
            maxLength={500}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button type="submit" variant="secondary" disabled={draft.trim().length < 2}>
            <SearchIcon aria-hidden />
            Search
          </Button>
        </form>
        {!query ? null : results.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : results.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(results.error)}</AlertDescription>
          </Alert>
        ) : results.data.results.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            No indexed passages match. Upload documents, or try other words.
          </p>
        ) : (
          <ol className="space-y-3" aria-label="Search results">
            {results.data.results.map((hit, rank) => {
              const page = pageLabel(hit.chunk);
              return (
                <li key={hit.chunk.id} className="rounded-lg border p-3">
                  <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">#{rank + 1}</span>
                    <Link
                      href={`/knowledge-base/${hit.document.id}`}
                      className="font-medium text-foreground hover:underline"
                    >
                      {hit.document.title}
                    </Link>
                    {page ? <span>{page}</span> : null}
                    {hit.chunk.section_heading ? <span>§ {hit.chunk.section_heading}</span> : null}
                    <Badge variant="outline" className="ml-auto tabular-nums" title="Cosine similarity">
                      {hit.score.toFixed(3)}
                    </Badge>
                  </div>
                  <p className="line-clamp-4 text-sm whitespace-pre-wrap">{hit.chunk.content}</p>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
