"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRightIcon,
  CircleAlertIcon,
  CornerDownLeftIcon,
  ListIcon,
  Loader2Icon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { AnswerSources, AnswerText } from "@/components/answers/answer-text";
import { FeedbackButtons } from "@/components/answers/feedback-buttons";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage, tenant, unwrap, type AIRun, type Ticket } from "@/lib/api/client";
import { ANSWER_STATUS, DECISION_REASONS, formatSeconds } from "@/lib/answers";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

/** The reply text without citation markers (the customer doesn't see the sources). */
export function replyText(text: string): string {
  return text
    .replace(/\s*\[\d+\]/g, "")
    .replace(/[ \t]+([.,;:!?])/g, "$1")
    .trim();
}

function useElapsed(startedAt: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  return startedAt === null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1000));
}

function RunFooter({ run }: { run: AIRun }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span>{run.answer?.model}</span>
      <span className="tabular-nums">{formatSeconds(run.latency_ms)}</span>
      <FeedbackButtons runId={run.id} />
      <Link
        href={`/ai-inspector/${run.id}`}
        className="ml-auto inline-flex items-center gap-0.5 font-medium text-foreground hover:underline"
      >
        Inspect
        <ChevronRightIcon className="size-3.5" aria-hidden />
      </Link>
    </div>
  );
}

function Suggestion({ run, onUse }: { run: AIRun; onUse: (text: string) => void }) {
  const prefix = useId();
  const answer = run.answer;
  if (!answer) return null;
  const status = ANSWER_STATUS[answer.status];
  const usable = (answer.status === "answered" || answer.status === "escalated") && answer.text;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">Suggested reply</h3>
        {answer.status !== "answered" ? (
          <Badge variant={status.tone === "warn" ? "destructive" : "secondary"} title={status.description}>
            {status.label}
          </Badge>
        ) : null}
      </div>
      {answer.status === "failed" ? (
        <p className="text-sm text-destructive">{answer.error}</p>
      ) : (
        <AnswerText answer={answer} sourceId={(n) => `${prefix}-source-${n}`} />
      )}
      {run.assessment && run.assessment.decision !== "answer" ? (
        <p className="text-xs text-muted-foreground">
          {answer.status === "escalated"
            ? "The documents disagree; check which source is right before replying."
            : (DECISION_REASONS[run.assessment.reason] ?? run.assessment.reason)}{" "}
          {answer.requires_human_review ? "Logged in the activity as an AI escalation." : null}
        </p>
      ) : null}
      {answer.citations.length ? (
        <AnswerSources answer={answer} passages={run.results} sourceId={(n) => `${prefix}-source-${n}`} />
      ) : null}
      {usable ? (
        <Button type="button" size="sm" variant="outline" onClick={() => onUse(replyText(answer.text ?? ""))}>
          <CornerDownLeftIcon aria-hidden />
          Use this reply
        </Button>
      ) : null}
      <RunFooter run={run} />
    </div>
  );
}

function Summary({ run }: { run: AIRun }) {
  const answer = run.answer;
  if (!answer) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">Summary</h3>
      {answer.status === "failed" ? (
        <p className="text-sm text-destructive">{answer.error}</p>
      ) : (
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{answer.text}</p>
      )}
      <RunFooter run={run} />
    </div>
  );
}

/**
 * Staff-only AI help on a ticket: a suggested reply from the knowledge base (with
 * sources, never sent automatically) and a summary of the conversation.
 */
export function TicketAIPanel({ ticket, onUseReply }: { ticket: Ticket; onUseReply: (text: string) => void }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const key = queryKeys.ticketAI(organization.id, ticket.id);
  const latest = useQuery({
    queryKey: key,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/ai", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
        }),
      ),
    staleTime: 30_000,
  });

  const params = { params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) } };
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: key, refetchType: "all" });
    // A suggestion can add an "AI escalated" event; runs show in the inspector.
    void queryClient.invalidateQueries({
      queryKey: queryKeys.ticketEvents(organization.id, ticket.id),
      refetchType: "all",
    });
    void queryClient.invalidateQueries({ queryKey: queryKeys.aiRuns(organization.id), refetchType: "all" });
  };
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const timing = {
    onMutate: () => setStartedAt(Date.now()),
    onSettled: () => setStartedAt(null),
    onSuccess: refresh,
  };
  const suggest = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/tickets/{ticket_id}/ai/suggestion", params)),
    ...timing,
  });
  const summarize = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/tickets/{ticket_id}/ai/summary", params)),
    ...timing,
  });
  const busy = suggest.isPending || summarize.isPending;
  const seconds = useElapsed(startedAt);
  const error = suggest.error ?? summarize.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <SparklesIcon className="size-4" aria-hidden />
          <h2>AI assistant</h2>
        </CardTitle>
        <CardDescription>
          Drafts from your knowledge base, with sources. Review before sending; nothing is sent
          automatically.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={() => suggest.mutate()} disabled={busy}>
            {suggest.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <SparklesIcon aria-hidden />}
            Suggest a reply
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => summarize.mutate()} disabled={busy}>
            {summarize.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <ListIcon aria-hidden />}
            Summarize
          </Button>
          {busy ? (
            <span className="text-xs text-muted-foreground tabular-nums" role="status">
              Working… {seconds} s
            </span>
          ) : null}
        </div>
        {error ? (
          <Alert variant="destructive">
            <CircleAlertIcon aria-hidden />
            <AlertTitle>The AI couldn&apos;t do that</AlertTitle>
            <AlertDescription>{errorMessage(error)}</AlertDescription>
          </Alert>
        ) : null}
        {latest.data?.summary ? <Summary run={latest.data.summary} /> : null}
        {latest.data?.suggestion ? <Suggestion run={latest.data.suggestion} onUse={onUseReply} /> : null}
      </CardContent>
    </Card>
  );
}
