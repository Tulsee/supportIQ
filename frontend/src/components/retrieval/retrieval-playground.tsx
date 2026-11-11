"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ScanSearchIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { PassageCard } from "@/components/retrieval/passage-card";
import { RunSummary } from "@/components/retrieval/run-summary";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { REQUESTED_STRATEGY_LABELS, type RequestedStrategy } from "@/lib/retrieval";

const STRATEGIES = Object.keys(REQUESTED_STRATEGY_LABELS) as RequestedStrategy[];

/**
 * Run the retrieval pipeline the AI assistant uses (keyword + semantic search,
 * fused and reranked) and show how every passage scored at each stage.
 */
export function RetrievalPlayground() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const rerankId = useId();
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState<RequestedStrategy>("auto");
  const [rerank, setRerank] = useState(true);
  const [showOthers, setShowOthers] = useState(false);

  const retrieve = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/ai/retrieve", {
          params: { header: tenant(organization.id) },
          body: { query: query.trim(), strategy, rerank, top_k: 5 },
        }),
      ),
    onSuccess: () => {
      setShowOthers(false);
      // Each run is recorded; the inspector list may be mounted but hidden.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.aiRuns(organization.id),
        refetchType: "all",
      });
    },
  });
  const run = retrieve.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Test retrieval</h2>
        </CardTitle>
        <CardDescription>
          Searches the knowledge base the way the AI assistant will: by keywords (BM25) and by
          meaning, combined and then reranked. Every run is saved in the AI inspector.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          role="search"
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim().length >= 2) retrieve.mutate();
          }}
        >
          <div className="flex gap-2">
            <Input
              aria-label="Search question"
              placeholder="e.g. How long do refunds take?"
              value={query}
              maxLength={500}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button
              type="submit"
              variant="secondary"
              disabled={query.trim().length < 2 || retrieve.isPending}
            >
              <SearchIcon aria-hidden />
              Search
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <Select
              items={REQUESTED_STRATEGY_LABELS}
              value={strategy}
              onValueChange={(value) => value && setStrategy(value as RequestedStrategy)}
            >
              <SelectTrigger aria-label="Search strategy" className="w-48" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STRATEGIES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {REQUESTED_STRATEGY_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center gap-2">
              <Switch id={rerankId} checked={rerank} onCheckedChange={setRerank} />
              <Label htmlFor={rerankId} className="font-normal">
                Rerank with the cross-encoder
              </Label>
            </div>
          </div>
        </form>

        {retrieve.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : retrieve.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(retrieve.error)}</AlertDescription>
          </Alert>
        ) : run ? (
          <div className="space-y-4">
            <RunSummary run={run} />
            {run.results.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No indexed passages match. Upload documents, or try other words.
              </p>
            ) : (
              <ol className="space-y-3" aria-label="Search results">
                {run.results.map((passage) => (
                  <PassageCard key={passage.fused_rank} passage={passage} />
                ))}
              </ol>
            )}
            {run.other_candidates.length ? (
              <div className="space-y-3">
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0"
                  aria-expanded={showOthers}
                  onClick={() => setShowOthers(!showOthers)}
                >
                  {showOthers ? "Hide" : "Show"} {run.other_candidates.length} other candidate
                  {run.other_candidates.length === 1 ? "" : "s"}
                </Button>
                {showOthers ? (
                  <ol className="space-y-3" aria-label="Other candidates">
                    {run.other_candidates.map((passage) => (
                      <PassageCard
                        key={passage.fused_rank}
                        passage={passage}
                        dimmed
                      />
                    ))}
                  </ol>
                ) : null}
              </div>
            ) : null}
            <Link
              href={`/ai-inspector/${run.id}`}
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              <ScanSearchIcon className="size-4" aria-hidden />
              Open this run in the AI inspector
            </Link>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
