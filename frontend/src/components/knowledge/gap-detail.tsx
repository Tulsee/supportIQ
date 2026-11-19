"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  CheckIcon,
  FilePenLineIcon,
  Loader2Icon,
  RefreshCwIcon,
  RotateCcwIcon,
  SendIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useId, useState } from "react";
import { toast } from "sonner";

import { DRAFT_LABELS, GapBadges, JobStatusLine, useGapSummary } from "@/components/knowledge/gaps-page";
import { NoWorkerAlert, useWorkerOffline } from "@/components/knowledge/worker-status";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type GapDraftStatement,
  type KnowledgeGap,
} from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const WHERE: Record<string, string> = {
  answer: "Ask page",
  suggestion: "Ticket suggestion",
  self_service: "Customer assistant",
};

const PROBLEMS: Record<GapDraftStatement["problems"][number], string> = {
  quote_not_found: "its quote isn't in the ticket's agent replies",
  number_not_in_quote: "it states a number its quote doesn't",
  private: "it names a customer or a reference",
  conflict: "it contradicts the knowledge base",
};

const VISIBILITY = { internal: "Internal (staff only)", public: "Public (customer assistant too)" } as const;
type Visibility = keyof typeof VISIBILITY;

/** One gap: its questions, the draft written from resolved tickets, and the article. */
export function GapDetail() {
  const { gapId } = useParams<{ gapId: string }>();
  const organization = useActiveOrganization();
  const isAdmin = organization.role === "admin";
  const queryClient = useQueryClient();
  const key = queryKeys.gap(organization.id, gapId);

  const gap = useQuery({
    queryKey: key,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/knowledge/gaps/{gap_id}", {
          params: { path: { gap_id: gapId }, header: tenant(organization.id) },
        }),
      ),
    staleTime: 0,
    // The article indexes in the background: follow it until it settles.
    refetchInterval: (current) => {
      const status = current.state.data?.article?.status;
      return status === "queued" || status === "processing" ? 3_000 : false;
    },
  });
  const { running, latestJob } = useGapSummary(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.gaps(organization.id), refetchType: "all" });
  });
  const thisJob = latestJob?.gap_id === gapId ? latestJob : null;
  const busy = running && thisJob?.status === "running";
  const workerOffline = useWorkerOffline(running);

  const startJob = useMutation({
    mutationFn: (kind: "drafts" | "rechecks") =>
      unwrap(
        kind === "drafts"
          ? api.POST("/api/v1/knowledge/gaps/{gap_id}/drafts", {
              params: { path: { gap_id: gapId }, header: tenant(organization.id) },
            })
          : api.POST("/api/v1/knowledge/gaps/{gap_id}/rechecks", {
              params: { path: { gap_id: gapId }, header: tenant(organization.id) },
            }),
      ),
    onSuccess: async (job) => {
      toast.success(job.kind === "draft" ? "Drafting the article" : "Asking the questions again");
      await queryClient.invalidateQueries({ queryKey: queryKeys.gapSummary(organization.id) });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const review = useMutation({
    mutationFn: (status: "open" | "dismissed") =>
      unwrap(
        api.PATCH("/api/v1/knowledge/gaps/{gap_id}", {
          params: { path: { gap_id: gapId }, header: tenant(organization.id) },
          body: { status },
        }),
      ),
    onSuccess: async (updated) => {
      toast.success(updated.status === "dismissed" ? "Dismissed" : "Reopened");
      queryClient.setQueryData(key, updated);
      await queryClient.invalidateQueries({ queryKey: queryKeys.gaps(organization.id), refetchType: "all" });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (gap.isPending) return <Skeleton className="h-64 w-full" />;
  if (gap.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(gap.error)}</AlertDescription>
      </Alert>
    );
  }
  const data = gap.data;
  const open = data.status === "open";
  const indexed = data.article?.status === "indexed";

  return (
    <>
      <PageHeader
        title={data.label}
        description={`Unanswered ${data.asked} time${data.asked === 1 ? "" : "s"}, ${data.question_count} distinct question${data.question_count === 1 ? "" : "s"}.`}
        actions={
          <>
            <Button variant="outline" render={<Link href="/knowledge-base/gaps" />}>
              <ArrowLeftIcon aria-hidden />
              Gaps
            </Button>
            {isAdmin && open ? (
              <>
                <Button
                  variant="ghost"
                  disabled={review.isPending}
                  onClick={() => review.mutate("dismissed")}
                >
                  <XIcon aria-hidden />
                  Dismiss
                </Button>
                <Button disabled={startJob.isPending || running} onClick={() => startJob.mutate("drafts")}>
                  {busy && thisJob?.kind === "draft" ? (
                    <Loader2Icon className="animate-spin" aria-hidden />
                  ) : (
                    <FilePenLineIcon aria-hidden />
                  )}
                  {data.draft ? "Draft again" : "Draft article"}
                </Button>
              </>
            ) : null}
            {isAdmin && data.status === "dismissed" ? (
              <Button variant="outline" disabled={review.isPending} onClick={() => review.mutate("open")}>
                <RotateCcwIcon aria-hidden />
                Reopen
              </Button>
            ) : null}
            {isAdmin && data.status === "published" ? (
              <Button
                variant="outline"
                disabled={!indexed || startJob.isPending || running}
                title={indexed ? undefined : "Available once the article is indexed"}
                onClick={() => startJob.mutate("rechecks")}
              >
                {busy && thisJob?.kind === "recheck" ? (
                  <Loader2Icon className="animate-spin" aria-hidden />
                ) : (
                  <RefreshCwIcon aria-hidden />
                )}
                Check again
              </Button>
            ) : null}
          </>
        }
      />
      <div className="space-y-6">
        {workerOffline ? <NoWorkerAlert /> : null}
        <div className="flex flex-wrap items-center gap-3">
          <GapBadges gap={data} />
          {thisJob ? <JobStatusLine job={thisJob} /> : null}
        </div>
        {data.status === "dismissed" ? (
          <p className="text-sm text-muted-foreground">
            Dismissed{data.reviewed_by ? ` by ${data.reviewed_by.full_name}` : ""}
            {data.reviewed_at ? ` ${timeAgo(data.reviewed_at)}` : ""}
            {data.note ? `: ${data.note}` : ""}. New phrasings of these questions still join it.
          </p>
        ) : null}
        {data.article ? <ArticleCard gap={data} /> : null}
        <QuestionsCard gap={data} />
        {data.draft ? <DraftCard gap={data} /> : null}
        {isAdmin && open ? <PublishCard key={data.draft?.drafted_at ?? "none"} gap={data} /> : null}
      </div>
    </>
  );
}

function QuestionsCard({ gap }: { gap: KnowledgeGap }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Questions</h2>
        </CardTitle>
        <CardDescription>As customers and staff asked them, most often first.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y">
          {gap.questions.map((q) => (
            <li key={q.query} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2">
              <span className="min-w-0 text-sm">{q.query}</span>
              <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {q.asked ? `×${q.asked}` : "not in the window"}
                {q.kinds.map((kind) => (
                  <Badge key={kind} variant="outline" className="font-normal">
                    {WHERE[kind] ?? kind}
                  </Badge>
                ))}
                {q.ticket_ids.map((id) => (
                  <Link key={id} href={`/tickets/${id}`} className="hover:underline">
                    Ticket
                  </Link>
                ))}
                {q.last_run_id ? (
                  <Link href={`/ai-inspector/${q.last_run_id}`} className="hover:underline">
                    Last run
                  </Link>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function DraftCard({ gap }: { gap: KnowledgeGap }) {
  const draft = gap.draft!;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Draft: {DRAFT_LABELS[draft.status]}</h2>
        </CardTitle>
        <CardDescription>
          {draft.status === "no_sources"
            ? "No resolved ticket with an agent's reply looked like these questions, so there was nothing to draft from. You can still write the article yourself below."
            : draft.status === "not_answered"
              ? "The agents' replies on the closest tickets don't answer these questions, or nothing they said passed the checks."
              : draft.status === "failed"
                ? `The model couldn't be used: ${draft.error ?? "unknown error"}`
                : "Written by the model from the agents' replies. Each statement must quote the reply it comes from; statements that fail a check are left out of the article."}
          {draft.drafted_at ? (
            <>
              {" "}
              Drafted{" "}
              <time dateTime={draft.drafted_at} title={formatDateTime(draft.drafted_at)}>
                {timeAgo(draft.drafted_at)}
              </time>
              {draft.model ? ` by ${draft.model}` : ""}.
            </>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {gap.sources.length ? (
          <div>
            <h3 className="mb-1 text-sm font-medium">Source tickets</h3>
            <ul className="space-y-1 text-sm">
              {gap.sources.map((source) => (
                <li key={source.id}>
                  <Link href={`/tickets/${source.id}`} className="hover:underline">
                    #{source.number} {source.subject}
                  </Link>{" "}
                  <span className="text-xs text-muted-foreground">
                    {source.via === "linked"
                      ? "(the questions led to it)"
                      : `(similar${source.similarity != null ? `, ${Math.round(source.similarity * 100)}%` : ""})`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {draft.conflicts.length ? (
          <Alert variant="destructive">
            <AlertTitle>Contradicts the knowledge base</AlertTitle>
            <AlertDescription>
              <ul className="space-y-2">
                {draft.conflicts.map((c, index) => (
                  <li key={index}>
                    The tickets say “{c.quote_draft}”, but{" "}
                    <Link href={`/knowledge-base/${c.document_id}`} className="font-medium underline">
                      {c.document_title}
                    </Link>{" "}
                    says “{c.quote_document}”. Left out: decide which is right before adding it.
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}
        {draft.statements.length ? (
          <div>
            <h3 className="mb-1 text-sm font-medium">
              Statements ({draft.statements.filter((s) => s.included).length} of {draft.statements.length} passed)
            </h3>
            <ol className="space-y-2">
              {draft.statements.map((s, index) => (
                <li key={index} className="rounded-md border p-3 text-sm">
                  <p className="flex items-start gap-2">
                    {s.included ? (
                      <CheckIcon className="mt-0.5 size-4 shrink-0 text-green-600" aria-label="Passed" />
                    ) : (
                      <XIcon className="mt-0.5 size-4 shrink-0 text-destructive" aria-label="Left out" />
                    )}
                    <span>{s.text}</span>
                  </p>
                  <blockquote className="mt-1 border-l-2 pl-2 text-xs text-muted-foreground">
                    Ticket #{s.ticket}: “{s.quote || "no quote"}”
                  </blockquote>
                  {s.included ? null : (
                    <p className="mt-1 text-xs text-destructive">
                      Left out: {s.problems.map((p) => PROBLEMS[p]).join("; ")}
                      {s.private.length ? ` (${s.private.join(", ")})` : ""}.
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {draft.passages_checked ? (
          <p className="text-xs text-muted-foreground">
            Checked against the {draft.passages_checked} most similar knowledge-base passage
            {draft.passages_checked === 1 ? "" : "s"} for contradictions.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PublishCard({ gap }: { gap: KnowledgeGap }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const id = useId();
  const [title, setTitle] = useState(gap.draft?.title || gap.label);
  const [body, setBody] = useState(gap.draft?.body ?? "");
  const [visibility, setVisibility] = useState<Visibility>("internal");
  const publish = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/knowledge/gaps/{gap_id}/publish", {
          params: { path: { gap_id: gap.id }, header: tenant(organization.id) },
          body: { title: title.trim(), body: body.trim(), visibility },
        }),
      ),
    onSuccess: async (updated) => {
      toast.success("Published: the article is being indexed");
      queryClient.setQueryData(queryKeys.gap(organization.id, gap.id), updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.gaps(organization.id), refetchType: "all" }),
        queryClient.invalidateQueries({ queryKey: queryKeys.documents(organization.id), refetchType: "all" }),
      ]);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const ready = title.trim().length > 0 && body.trim().length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Article</h2>
        </CardTitle>
        <CardDescription>
          Edit before publishing: it becomes a knowledge-base document like any other. Check every fact; the
          agents&apos; replies were right for their customers, not necessarily for everyone.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor={`${id}-title`}>Title</Label>
          <Input id={`${id}-title`} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${id}-body`}>Text (Markdown)</Label>
          <Textarea
            id={`${id}-body`}
            value={body}
            rows={8}
            maxLength={20_000}
            placeholder="Write the answer to these questions."
            onChange={(e) => setBody(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <Label htmlFor={`${id}-visibility`}>Who can it answer</Label>
            <Select items={VISIBILITY} value={visibility} onValueChange={(v) => v && setVisibility(v as Visibility)}>
              <SelectTrigger id={`${id}-visibility`} className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(VISIBILITY) as Visibility[]).map((value) => (
                  <SelectItem key={value} value={value}>
                    {VISIBILITY[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button disabled={!ready || publish.isPending} onClick={() => publish.mutate()}>
            {publish.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <SendIcon aria-hidden />}
            Publish
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ArticleCard({ gap }: { gap: KnowledgeGap }) {
  const article = gap.article!;
  const recheck = gap.recheck;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Published article</h2>
        </CardTitle>
        <CardDescription>
          {article.document_id ? (
            <Link href={`/knowledge-base/${article.document_id}`} className="font-medium underline">
              {article.title}
            </Link>
          ) : (
            "The document was deleted."
          )}
          {article.status ? ` · ${article.status}` : ""}
          {article.visibility ? ` · ${article.visibility}` : ""}
          {article.published_at ? (
            <>
              {" "}
              · published{article.published_by ? ` by ${article.published_by.full_name}` : ""}{" "}
              <time dateTime={article.published_at} title={formatDateTime(article.published_at)}>
                {timeAgo(article.published_at)}
              </time>
            </>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {gap.asked_since_published ? (
          <p>
            {gap.asked_since_published} of these questions went unanswered again after publishing: the
            article may need more.
          </p>
        ) : null}
        {recheck ? (
          <>
            <p>
              Asked again{" "}
              {recheck.checked_at ? (
                <time dateTime={recheck.checked_at} title={formatDateTime(recheck.checked_at)}>
                  {timeAgo(recheck.checked_at)}
                </time>
              ) : null}
              : <strong>{recheck.answered}</strong> of {recheck.total} answered,{" "}
              {recheck.cited_article} citing the article.
            </p>
            <ul className="divide-y">
              {recheck.questions.map((q) => (
                <li key={q.run_id} className="flex flex-wrap items-baseline justify-between gap-2 py-1.5">
                  <span>{q.query}</span>
                  <span className="text-xs text-muted-foreground">
                    {q.before ?? "?"} → {q.after ?? "?"}
                    {q.cited_article ? " · cites the article" : ""} ·{" "}
                    <Link href={`/ai-inspector/${q.run_id}`} className="hover:underline">
                      run
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-muted-foreground">
            Once the article is indexed, Check again asks these questions again to see whether they are answered
            now.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
