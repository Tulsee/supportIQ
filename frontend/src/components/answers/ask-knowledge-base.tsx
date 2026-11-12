"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  SquarePenIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { AnswerSources, AnswerText } from "@/components/answers/answer-text";
import { FeedbackButtons } from "@/components/answers/feedback-buttons";
import { ModelStatusAlert, useModelStatus } from "@/components/answers/model-status";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, tenant, unwrap, type AIRun } from "@/lib/api/client";
import { ANSWER_STATUS, answeredBy, DECISION_REASONS, formatSeconds, modelLabel } from "@/lib/answers";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

const MAX_QUESTION = 500; // the API's limit

type HistoryTurn = { question: string; answer: string };

type Exchange = {
  id: string;
  question: string;
  startedAt: number;
  run?: AIRun;
  error?: string;
};

function useElapsedSeconds(since: number, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return Math.max(0, Math.floor((now - since) / 1000));
}

function Thinking({ since, local }: { since: number; local: boolean }) {
  const seconds = useElapsedSeconds(since, true);
  return (
    <div className="flex items-start gap-2 text-sm text-muted-foreground" role="status">
      <Loader2Icon className="mt-0.5 size-4 shrink-0 animate-spin" aria-hidden />
      <div>
        <p>
          Searching your documents and writing an answer…{" "}
          <span className="tabular-nums">{seconds} s</span>
        </p>
        {local && seconds >= 15 ? (
          <p className="text-xs">A model running on this server can take a minute or more.</p>
        ) : null}
      </div>
    </div>
  );
}

function AnswerReply({ run }: { run: AIRun }) {
  const prefix = useId();
  const sourceId = (n: number) => `${prefix}-source-${n}`;
  const answer = run.answer;
  if (!answer) {
    return <p className="text-sm text-muted-foreground">No answer was recorded for this question.</p>;
  }
  const status = ANSWER_STATUS[answer.status];

  return (
    <div className="space-y-3">
      {answer.status === "failed" ? (
        <Alert variant="destructive">
          <CircleAlertIcon aria-hidden />
          <AlertTitle>No answer</AlertTitle>
          <AlertDescription>{answer.error ?? status.description}</AlertDescription>
        </Alert>
      ) : (
        <>
          {answer.status === "escalated" ? (
            <Alert>
              <CircleAlertIcon aria-hidden />
              <AlertTitle>The documents disagree</AlertTitle>
              <AlertDescription>
                Here is what each source says. A team member should confirm which is right.
              </AlertDescription>
            </Alert>
          ) : answer.status !== "answered" ? (
            <Badge variant={status.tone === "warn" ? "destructive" : "secondary"} title={status.description}>
              {status.label}
            </Badge>
          ) : null}
          <AnswerText answer={answer} sourceId={sourceId} />
          {run.assessment && run.assessment.decision !== "answer" && answer.status !== "escalated" ? (
            <p className="text-xs text-muted-foreground">
              {DECISION_REASONS[run.assessment.reason] ?? run.assessment.reason}
              {answer.status === "clarify" ? " Ask again with more detail below." : ""}
            </p>
          ) : null}
          {answer.status === "uncited" ? (
            <p className="text-xs text-muted-foreground">{status.description}</p>
          ) : null}
          {answer.citations.length ? (
            <div className="space-y-1.5 border-t pt-3">
              <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Sources
              </h3>
              <AnswerSources answer={answer} passages={run.results} sourceId={sourceId} />
            </div>
          ) : null}
        </>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>{answeredBy(answer)}</span>
        <span className="tabular-nums">{formatSeconds(run.latency_ms)}</span>
        <FeedbackButtons runId={run.id} />
        <Link
          href={`/ai-inspector/${run.id}`}
          className="ml-auto inline-flex items-center gap-0.5 font-medium text-foreground hover:underline"
        >
          Inspect run
          <ChevronRightIcon className="size-3.5" aria-hidden />
        </Link>
      </div>
    </div>
  );
}

function ExchangeView({ exchange, local }: { exchange: Exchange; local: boolean }) {
  return (
    <li className="space-y-3">
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
          {exchange.question}
        </p>
      </div>
      <div className="rounded-xl border bg-card p-4 shadow-xs">
        {exchange.run ? (
          <AnswerReply run={exchange.run} />
        ) : exchange.error ? (
          <p className="flex items-start gap-2 text-sm text-destructive">
            <CircleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            {exchange.error}
          </p>
        ) : (
          <Thinking since={exchange.startedAt} local={local} />
        )}
      </div>
    </li>
  );
}

/**
 * Ask the knowledge base: answers come only from the organization's documents and
 * cite the passages they use. Each question is answered on its own (no follow-ups
 * yet); every answer is recorded in the AI inspector.
 */
export function AskKnowledgeBase() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const model = useModelStatus();
  const [draft, setDraft] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: ({ query, history }: { query: string; history: HistoryTurn[] }) =>
      unwrap(
        api.POST("/api/v1/ai/chat", {
          params: { header: tenant(organization.id) },
          body: { query, strategy: "auto", rerank: true, top_k: 5, history },
        }),
      ),
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [exchanges]);

  const update = (id: string, change: Partial<Exchange>) =>
    setExchanges((previous) => previous.map((e) => (e.id === id ? { ...e, ...change } : e)));

  const question = draft.trim();
  const canAsk = question.length >= 2 && !ask.isPending;

  async function submit() {
    if (!canAsk) return;
    const id = crypto.randomUUID();
    // The last few turns, so a follow-up ("and for the camera?") is understood.
    const history = exchanges
      .filter((e) => e.run?.answer?.text)
      .slice(-3)
      .map((e) => ({ question: e.question, answer: e.run?.answer?.text ?? "" }));
    setExchanges((previous) => [...previous, { id, question, startedAt: Date.now() }]);
    setDraft("");
    try {
      update(id, { run: await ask.mutateAsync({ query: question, history }) });
      // Every answer is recorded; the inspector list may be mounted but hidden.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.aiRuns(organization.id),
        refetchType: "all",
      });
    } catch (error) {
      update(id, { error: errorMessage(error) });
    }
    // A failure may mean the model just went away.
    void queryClient.invalidateQueries({ queryKey: queryKeys.aiModel(organization.id) });
  }

  const local = model.data?.provider === "ollama";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col">
      <PageHeader
        title="Ask the knowledge base"
        description={
          <>
            Answers come only from your documents and cite the passages they use. Follow-up
            questions are understood in context.
            {model.data ? (
              <span className="mt-1 block text-xs">Answered by {modelLabel(model.data)}</span>
            ) : null}
          </>
        }
        actions={
          exchanges.length > 0 ? (
            // Later questions are read in the context of earlier ones; this starts afresh.
            <Button variant="outline" onClick={() => setExchanges([])} disabled={ask.isPending}>
              <SquarePenIcon aria-hidden />
              New conversation
            </Button>
          ) : null
        }
      />

      {model.data ? (
        <div className="mb-4">
          <ModelStatusAlert status={model.data} />
        </div>
      ) : null}

      {exchanges.length === 0 ? (
        <Empty className="mb-4 border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <MessageSquareTextIcon aria-hidden />
            </EmptyMedia>
            <EmptyTitle>Ask about anything in your documents</EmptyTitle>
            <EmptyDescription>
              For example, how to fix an error, what a policy says, or how a setting works. Open
              any answer in the AI inspector to see the passages and the exact prompt the model
              was given.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ol className="mb-4 space-y-6" aria-label="Questions and answers">
          {exchanges.map((exchange) => (
            <ExchangeView key={exchange.id} exchange={exchange} local={local} />
          ))}
        </ol>
      )}
      <div ref={endRef} />

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="sticky bottom-4 space-y-2 rounded-xl border bg-card p-3 shadow-sm"
      >
        <Textarea
          aria-label="Your question"
          placeholder="Ask a question…"
          rows={2}
          maxLength={MAX_QUESTION}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }}
          className="max-h-48 resize-none border-0 bg-transparent px-1 shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="hidden text-xs text-muted-foreground sm:inline">
            Enter to ask · Shift+Enter for a new line
          </span>
          <span
            className={cn(
              "text-xs text-muted-foreground tabular-nums",
              draft.length < MAX_QUESTION - 100 && "sr-only",
            )}
          >
            {draft.length}/{MAX_QUESTION}
          </span>
          <Button type="submit" disabled={!canAsk} className="ml-auto">
            {ask.isPending ? (
              <Loader2Icon className="animate-spin" aria-hidden />
            ) : (
              <ArrowUpIcon aria-hidden />
            )}
            Ask
          </Button>
        </div>
      </form>
    </div>
  );
}
