"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useId } from "react";

import { AnswerCard, PromptCard } from "@/components/answers/answer-details";
import { AssessmentCard } from "@/components/answers/assessment-card";
import { FeedbackButtons } from "@/components/answers/feedback-buttons";
import { PassageCard } from "@/components/retrieval/passage-card";
import { RunSummary } from "@/components/retrieval/run-summary";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, errorMessage, tenant, unwrap, type AIRun } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { REQUESTED_STRATEGY_LABELS, type RequestedStrategy, formatMs } from "@/lib/retrieval";
import { formatDateTime } from "@/lib/tickets";

function Setting({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words tabular-nums">{children}</dd>
    </div>
  );
}

/** The settings a run used: enough to reproduce it. */
function RunConfig({ run }: { run: AIRun }) {
  const config = run.config as {
    top_k?: number;
    sparse_top_k?: number;
    dense_top_k?: number;
    candidate_multiplier?: number;
    rrf_k?: number;
    rerank?: boolean;
    rerank_candidates?: number;
    bm25?: { k1: number; b: number };
    embedding_model?: string;
    reranker_model?: string | null;
  };
  const multiplier = config.candidate_multiplier ?? 1;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Configuration</h2>
        </CardTitle>
        <CardDescription>The retrieval settings this run used.</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Setting label="Requested strategy">
            {REQUESTED_STRATEGY_LABELS[run.requested_strategy as RequestedStrategy] ??
              run.requested_strategy}
          </Setting>
          <Setting label="Passages selected">{config.top_k ?? "—"}</Setting>
          <Setting label="BM25 candidates">
            {config.sparse_top_k ?? "—"}
            {multiplier > 1 ? ` (×${multiplier})` : ""}
          </Setting>
          <Setting label="Semantic candidates">
            {config.dense_top_k ?? "—"}
            {multiplier > 1 ? ` (×${multiplier})` : ""}
          </Setting>
          <Setting label="Fusion">Reciprocal Rank Fusion, k = {config.rrf_k ?? "—"}</Setting>
          <Setting label="BM25 parameters">
            {config.bm25 ? `k1 = ${config.bm25.k1}, b = ${config.bm25.b}` : "—"}
          </Setting>
          <Setting label="Embedding model">{config.embedding_model ?? "—"}</Setting>
          <Setting label="Reranker">
            {config.reranker_model
              ? `${config.reranker_model} (top ${config.rerank_candidates ?? "—"})`
              : config.rerank
                ? "Unavailable"
                : "Off"}
          </Setting>
        </dl>
      </CardContent>
    </Card>
  );
}

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const organization = useActiveOrganization();
  // Per instance: a hidden, still-mounted copy of this page must not share anchors.
  const prefix = useId();
  const passageId = (rank: number) => `${prefix}-passage-${rank}`;

  const run = useQuery({
    queryKey: queryKeys.aiRun(organization.id, id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/ai/runs/{run_id}", {
          params: { path: { run_id: id }, header: tenant(organization.id) },
        }),
      ),
    // A run never changes after it is recorded (only its passages can be re-indexed away).
    staleTime: 60_000,
  });

  const backLink = (
    <Link
      href="/ai-inspector"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeftIcon className="size-4" aria-hidden />
      AI inspector
    </Link>
  );

  if (run.isPending) {
    return (
      <div className="space-y-4">
        {backLink}
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (run.isError) {
    const notFound =
      run.error instanceof ApiError && (run.error.status === 404 || run.error.status === 422);
    return (
      <div className="space-y-4">
        {backLink}
        <Alert variant={notFound ? "default" : "destructive"}>
          <AlertTitle>{notFound ? "Run not found" : "Couldn't load this run"}</AlertTitle>
          <AlertDescription>
            {notFound ? "It may belong to another organization." : errorMessage(run.error)}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = run.data;
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        {backLink}
        <h1 className="text-2xl font-semibold tracking-tight break-words">“{data.query}”</h1>
        <p className="text-sm text-muted-foreground">
          {data.user?.full_name ?? "Deleted user"} ·{" "}
          <time dateTime={data.created_at}>{formatDateTime(data.created_at)}</time> ·{" "}
          {formatMs(data.latency_ms)}
          {data.answer ? <FeedbackButtons runId={data.id} className="ml-2 align-middle" /> : null}
        </p>
      </div>

      {data.answer ? <AnswerCard answer={data.answer} passageId={passageId} /> : null}
      {data.assessment ? <AssessmentCard assessment={data.assessment} /> : null}
      {data.kind === "answer" && !data.answer ? (
        <Alert>
          <AlertTitle>No answer was recorded</AlertTitle>
          <AlertDescription>
            The server stopped while the model was writing it. The search below was recorded.
          </AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Query analysis</h2>
          </CardTitle>
          <CardDescription>How the query was classified and searched, and how long each stage took.</CardDescription>
        </CardHeader>
        <CardContent>
          <RunSummary run={data} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Selected passages ({data.results.length})</h2>
          </CardTitle>
          <CardDescription>
            {data.kind === "answer"
              ? "What the model was given, best first. Passage numbers are the citation numbers."
              : "What the AI assistant would read, best first."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {data.results.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              Nothing matched this query.
            </p>
          ) : (
            <ol className="space-y-3" aria-label="Selected passages">
              {data.results.map((passage) => (
                <PassageCard
                  key={passage.fused_rank}
                  passage={passage}
                  id={passage.final_rank ? passageId(passage.final_rank) : undefined}
                />
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {data.other_candidates.length ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Other candidates ({data.other_candidates.length})</h2>
            </CardTitle>
            <CardDescription>Found by a retriever but not selected, by fused rank.</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="space-y-3" aria-label="Other candidates">
              {data.other_candidates.map((passage) => (
                <PassageCard key={passage.fused_rank} passage={passage} dimmed />
              ))}
            </ol>
          </CardContent>
        </Card>
      ) : null}

      {data.answer ? <PromptCard answer={data.answer} /> : null}

      <RunConfig run={data} />
    </div>
  );
}
