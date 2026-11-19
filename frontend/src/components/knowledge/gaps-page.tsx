"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, FileQuestionIcon, Loader2Icon, SearchCheckIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { NoWorkerAlert, useWorkerOffline } from "@/components/knowledge/worker-status";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type KnowledgeGap,
  type KnowledgeGapJob,
} from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 10;
const POLL_MS = 3_000;

const STATUS_FILTERS = {
  open: "Open",
  published: "Published",
  dismissed: "Dismissed",
  all: "All gaps",
} as const;
type StatusFilter = keyof typeof STATUS_FILTERS;

export const DRAFT_LABELS: Record<NonNullable<KnowledgeGap["draft"]>["status"], string> = {
  drafted: "Draft ready",
  no_sources: "No ticket answers it",
  not_answered: "Tickets don't answer it",
  failed: "Draft failed",
};

/** The latest gap job, kept fresh while one runs; tells callers when it settles. */
export function useGapSummary(onSettled?: () => void) {
  const organization = useActiveOrganization();
  const summary = useQuery({
    queryKey: queryKeys.gapSummary(organization.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/knowledge/gaps/summary", { params: { header: tenant(organization.id) } }),
      ),
    staleTime: 0,
    refetchInterval: (current) =>
      current.state.data?.latest_job?.status === "running" ? POLL_MS : false,
  });
  const running = summary.data?.latest_job?.status === "running";
  const wasRunning = useRef(false);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  });
  useEffect(() => {
    if (wasRunning.current && !running) settled.current?.();
    wasRunning.current = running;
  }, [running]);
  return { summary, running, latestJob: summary.data?.latest_job ?? null };
}

/** Questions the assistant couldn't answer, grouped by topic, to turn into articles. */
export function GapsPage() {
  const organization = useActiveOrganization();
  const isAdmin = organization.role === "admin";
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>("open");
  const [offset, setOffset] = useState(0);
  const query = { limit: LIMIT, offset, status: status === "all" ? undefined : status };

  const { summary, running, latestJob } = useGapSummary(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.gaps(organization.id), refetchType: "all" });
  });
  const gaps = useQuery({
    queryKey: queryKeys.gaps(organization.id, query),
    queryFn: () =>
      unwrap(api.GET("/api/v1/knowledge/gaps", { params: { query, header: tenant(organization.id) } })),
    placeholderData: keepPreviousData,
    staleTime: 0,
  });
  const workerOffline = useWorkerOffline(running);

  const analyze = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/knowledge/gaps/analyses", { params: { header: tenant(organization.id) } })),
    onSuccess: async () => {
      toast.success("Looking for gaps");
      await queryClient.invalidateQueries({ queryKey: queryKeys.gapSummary(organization.id) });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const neverAnalyzed = summary.data && !summary.data.latest_analysis;

  return (
    <>
      <PageHeader
        title="Knowledge gaps"
        description="Questions the assistant couldn't answer, grouped by topic. Draft an article from the agents' replies on resolved tickets, check it, and publish it to the knowledge base."
        actions={
          <>
            <Button variant="outline" render={<Link href="/knowledge-base" />}>
              <ArrowLeftIcon aria-hidden />
              Knowledge base
            </Button>
            {isAdmin ? (
              <Button onClick={() => analyze.mutate()} disabled={analyze.isPending || running}>
                {analyze.isPending || running ? (
                  <Loader2Icon className="animate-spin" aria-hidden />
                ) : (
                  <SearchCheckIcon aria-hidden />
                )}
                Find gaps
              </Button>
            ) : null}
          </>
        }
      />
      <div className="space-y-6">
        {workerOffline ? <NoWorkerAlert /> : null}
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Unanswered questions</h2>
            </CardTitle>
            <CardDescription>
              Questions the assistant declined or abstained on (staff questions, ticket suggestions and the
              customer assistant), grouped by meaning. Questions where documents disagree are left to the
              contradiction scanner.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {summary.isPending ? (
              <Skeleton className="h-16 w-full" />
            ) : summary.isError ? (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(summary.error)}</AlertDescription>
              </Alert>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
                  <Stat label="Open gaps" value={summary.data.open} />
                  <Stat label="Drafts ready" value={summary.data.drafted} />
                  <Stat label="Published" value={summary.data.published} />
                  <Stat label="Dismissed" value={summary.data.dismissed} />
                  <Stat
                    label={`Questions (last ${summary.data.lookback_days} days)`}
                    value={summary.data.questions}
                  />
                </dl>
                <JobStatusLine job={latestJob} />
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="space-y-4">
            <Select
              items={STATUS_FILTERS}
              value={status}
              onValueChange={(value) => {
                if (!value) return;
                setStatus(value as StatusFilter);
                setOffset(0);
              }}
            >
              <SelectTrigger aria-label="Status" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_FILTERS) as StatusFilter[]).map((value) => (
                  <SelectItem key={value} value={value}>
                    {STATUS_FILTERS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {gaps.isPending ? (
              <div className="space-y-2">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : gaps.isError ? (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(gaps.error)}</AlertDescription>
              </Alert>
            ) : gaps.data.items.length === 0 ? (
              <Empty className="py-12">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <FileQuestionIcon aria-hidden />
                  </EmptyMedia>
                  <EmptyTitle>{status === "open" ? "No open gaps" : "Nothing here"}</EmptyTitle>
                  <EmptyDescription>
                    {neverAnalyzed
                      ? isAdmin
                        ? "Press Find gaps to group the questions the assistant couldn't answer."
                        : "Nobody has looked for gaps yet. An admin can."
                      : status === "open"
                        ? "Every unanswered question is in a published or dismissed gap."
                        : `No ${STATUS_FILTERS[status].toLowerCase()} gaps.`}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <>
                <ul className="space-y-3" aria-busy={gaps.isFetching}>
                  {gaps.data.items.map((gap) => (
                    <li key={gap.id}>
                      <GapCard gap={gap} />
                    </li>
                  ))}
                </ul>
                <Pager total={gaps.data.total} limit={LIMIT} offset={offset} onOffsetChange={setOffset} />
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

const JOB_NAMES: Record<KnowledgeGapJob["kind"], string> = {
  analyze: "Finding gaps",
  draft: "Drafting an article",
  recheck: "Asking the questions again",
};

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function JobStatusLine({ job }: { job: KnowledgeGapJob | null }) {
  if (!job) return <p className="text-sm text-muted-foreground">No gap job has run yet.</p>;
  const when = (
    <time dateTime={job.started_at} title={formatDateTime(job.started_at)}>
      {timeAgo(job.started_at)}
    </time>
  );
  if (job.status === "running") {
    return (
      <p className="flex items-center gap-2 text-sm" role="status">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        <span>
          {JOB_NAMES[job.kind]} (started {when}).
          {job.kind === "analyze" ? null : " The model can take a minute or more."}
        </span>
      </p>
    );
  }
  if (job.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {JOB_NAMES[job.kind]} failed ({when}): {job.error ?? "unknown error"}.
        </AlertDescription>
      </Alert>
    );
  }
  const r = job.result as Record<string, number | string>;
  const detail =
    job.kind === "analyze"
      ? `${plural(Number(r.questions ?? 0), "question")}, ${plural(Number(r.new_questions ?? 0), "new one")}: ${Number(r.joined ?? 0)} joined existing gaps, ${plural(Number(r.gaps_created ?? 0), "new gap")}${Number(r.left_for_later) ? `; ${Number(r.left_for_later)} left for next time` : ""}`
      : job.kind === "draft"
        ? `${plural(Number(r.sources ?? 0), "source ticket")}, ${Number(r.included ?? 0)} of ${plural(Number(r.statements ?? 0), "statement")} passed the checks`
        : `${Number(r.answered ?? 0)} of ${plural(Number(r.total ?? 0), "question")} answered now`;
  return (
    <p className="text-sm text-muted-foreground">
      Last job {when}: {JOB_NAMES[job.kind].toLowerCase()}, {detail}.
    </p>
  );
}

function GapCard({ gap }: { gap: KnowledgeGap }) {
  const more = gap.question_count - gap.questions.length;
  return (
    <article className="rounded-lg border p-4" aria-label={gap.label}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <h3 className="font-medium">
            <Link href={`/knowledge-base/gaps/${gap.id}`} className="hover:underline">
              {gap.label}
            </Link>
          </h3>
          <p className="text-xs text-muted-foreground">
            Unanswered {plural(gap.asked, "time")} · {plural(gap.question_count, "question")}
            {gap.last_asked ? (
              <>
                {" "}
                · last{" "}
                <time dateTime={gap.last_asked} title={formatDateTime(gap.last_asked)}>
                  {timeAgo(gap.last_asked)}
                </time>
              </>
            ) : null}
          </p>
        </div>
        <GapBadges gap={gap} />
      </div>
      {gap.questions.length > 1 || more > 0 ? (
        <ul className="mt-2 space-y-0.5 text-sm text-muted-foreground">
          {gap.questions.slice(1).map((q) => (
            <li key={q.query} className="truncate">
              “{q.query}”{q.asked > 1 ? ` ×${q.asked}` : ""}
            </li>
          ))}
          {more > 0 ? <li>and {plural(more, "more question")}</li> : null}
        </ul>
      ) : null}
    </article>
  );
}

export function GapBadges({ gap }: { gap: KnowledgeGap }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {gap.status === "published" ? (
        <Badge variant="secondary">Published</Badge>
      ) : gap.status === "dismissed" ? (
        <Badge variant="outline">Dismissed</Badge>
      ) : gap.draft ? (
        <Badge variant={gap.draft.status === "drafted" ? "default" : "outline"}>
          {DRAFT_LABELS[gap.draft.status]}
        </Badge>
      ) : (
        <Badge variant="outline">Open</Badge>
      )}
      {gap.status === "published" && gap.asked_since_published ? (
        <Badge variant="destructive">{plural(gap.asked_since_published, "question")} still unanswered</Badge>
      ) : null}
    </div>
  );
}
