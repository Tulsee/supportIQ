"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpIcon,
  CircleCheckIcon,
  CircleHelpIcon,
  HeadsetIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  SquarePenIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type AssistantConversation,
  type AssistantMessage,
  type AssistantSource,
} from "@/lib/api/client";
import { answerParts } from "@/lib/answers";
import { pageLabel } from "@/lib/documents";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { timeAgo } from "@/lib/tickets";
import { cn } from "@/lib/utils";

const MAX_QUESTION = 500; // the API's limit
const MAX_NOTE = 2000;
const RECENT = 8;

function useElapsedSeconds(since: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return Math.max(0, Math.floor((now - since) / 1000));
}

function Thinking({ since }: { since: number }) {
  const seconds = useElapsedSeconds(since);
  return (
    <div className="flex items-start gap-2 text-sm text-muted-foreground" role="status">
      <Loader2Icon className="mt-0.5 size-4 shrink-0 animate-spin" aria-hidden />
      <div>
        <p>Looking through our help articles…</p>
        {seconds >= 15 ? <p className="text-xs">This can take up to a minute.</p> : null}
      </div>
    </div>
  );
}

function sourceLabel(source: AssistantSource): string {
  return [source.title, pageLabel(source), source.section_heading ? `§ ${source.section_heading}` : null]
    .filter(Boolean)
    .join(", ");
}

/** Customers see sources numbered 1..n in the order listed; the stored markers are
 * the passages' ranks in the AI run, which mean nothing to them. */
function displayNumbers(message: AssistantMessage): Map<number, number> {
  return new Map(message.sources.map((s, index) => [s.passage_number, index + 1]));
}

function ReplyText({ message, sourceId }: { message: AssistantMessage; sourceId: (n: number) => string }) {
  const sources = new Map(message.sources.map((s) => [s.passage_number, s]));
  const numbers = displayNumbers(message);
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">
      {answerParts(message.body).map((part, index) => {
        if (part.kind === "text") return <span key={index}>{part.text}</span>;
        const source = sources.get(part.passage);
        const number = numbers.get(part.passage);
        if (!source || number === undefined) return null;
        const label = `Source ${number}: ${sourceLabel(source)}`;
        return (
          <a
            key={index}
            href={`#${sourceId(number)}`}
            aria-label={label}
            title={label}
            className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded bg-primary/10 px-1 align-text-top text-[11px] font-semibold text-primary tabular-nums no-underline hover:bg-primary/20"
          >
            {number}
          </a>
        );
      })}
    </p>
  );
}

function Rating({
  conversation,
  message,
}: {
  conversation: AssistantConversation;
  message: AssistantMessage;
}) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const rate = useMutation({
    mutationFn: (helpful: boolean) =>
      unwrap(
        api.POST("/api/v1/assistant/conversations/{conversation_id}/feedback", {
          params: { path: { conversation_id: conversation.id }, header: tenant(organization.id) },
          body: { message_id: message.id, helpful },
        }),
      ),
    onSuccess: async (updated) => {
      queryClient.setQueryData(queryKeys.assistantConversation(organization.id, updated.id), updated);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.assistantConversations(organization.id),
        refetchType: "all",
      });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (conversation.status === "handed_off") return null;
  if (message.rating === "helpful") {
    return (
      <p className="flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-400">
        <CircleCheckIcon className="size-3.5" aria-hidden />
        Glad that helped.
        <Button variant="link" size="xs" className="h-auto p-0" onClick={() => rate.mutate(false)}>
          Undo
        </Button>
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>{message.rating === "not_helpful" ? "Sorry about that." : "Did this answer your question?"}</span>
      {message.rating === "not_helpful" ? null : (
        <>
          <Button size="xs" variant="outline" disabled={rate.isPending} onClick={() => rate.mutate(true)}>
            <ThumbsUpIcon aria-hidden />
            Yes
          </Button>
          <Button size="xs" variant="outline" disabled={rate.isPending} onClick={() => rate.mutate(false)}>
            <ThumbsDownIcon aria-hidden />
            No
          </Button>
        </>
      )}
    </div>
  );
}

function Reply({ conversation, message }: { conversation: AssistantConversation; message: AssistantMessage }) {
  const prefix = useId();
  const sourceId = (n: number) => `${prefix}-source-${n}`;
  if (message.outcome === "unable") {
    return (
      <div className="flex items-start gap-2 text-sm text-muted-foreground">
        <CircleHelpIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p>{message.body}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <ReplyText message={message} sourceId={sourceId} />
      {message.sources.length ? (
        <div className="space-y-1 border-t pt-3">
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Sources</h3>
          <ol className="space-y-0.5 text-xs text-muted-foreground">
            {message.sources.map((source, index) => (
              <li key={source.passage_number} id={sourceId(index + 1)} className="flex gap-1.5">
                <span className="font-semibold text-primary tabular-nums">{index + 1}</span>
                <span>{sourceLabel(source)}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {message.outcome === "answered" ? <Rating conversation={conversation} message={message} /> : null}
      {message.outcome === "clarify" ? (
        <p className="text-xs text-muted-foreground">Reply below with a little more detail.</p>
      ) : null}
    </div>
  );
}

function needsPerson(conversation: AssistantConversation): boolean {
  if (conversation.status !== "open") return false;
  const last = conversation.messages.at(-1);
  return last?.role === "assistant" && (last.outcome === "unable" || last.rating === "not_helpful");
}

function ContactSupport({
  conversation,
  onSent,
}: {
  conversation: AssistantConversation;
  onSent: (updated: AssistantConversation) => void;
}) {
  const organization = useActiveOrganization();
  const noteId = useId();
  const [note, setNote] = useState("");
  const handoff = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/assistant/conversations/{conversation_id}/handoff", {
          params: { path: { conversation_id: conversation.id }, header: tenant(organization.id) },
          body: { note: note.trim() || null },
        }),
      ),
    onSuccess: onSent,
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card size="sm" className="border-primary/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HeadsetIcon className="size-4" aria-hidden />
          <h2>Contact support</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          We&apos;ll open a request with your questions, and a member of our team will reply there.
        </p>
        <div className="space-y-1.5">
          <Label htmlFor={noteId}>Anything to add? (optional)</Label>
          <Textarea
            id={noteId}
            rows={2}
            maxLength={MAX_NOTE}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="For example, your order number or what you've tried"
          />
        </div>
        <Button onClick={() => handoff.mutate()} disabled={handoff.isPending}>
          {handoff.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <HeadsetIcon aria-hidden />}
          Send to support
        </Button>
      </CardContent>
    </Card>
  );
}

function RecentConversations({
  activeId,
  onOpen,
}: {
  activeId: string | null;
  onOpen: (id: string) => void;
}) {
  const organization = useActiveOrganization();
  const page = { limit: RECENT, offset: 0 };
  const conversations = useQuery({
    queryKey: queryKeys.assistantConversations(organization.id, page),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/assistant/conversations", {
          params: { query: page, header: tenant(organization.id) },
        }),
      ),
  });
  if (conversations.isPending) return <Skeleton className="h-24 w-full" />;
  if (conversations.isError || conversations.data.items.length === 0) return null;
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2>Recent questions</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="-mx-2 space-y-0.5">
          {conversations.data.items.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onOpen(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
                className={cn(
                  "w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                  c.id === activeId && "bg-muted",
                )}
              >
                <span className="line-clamp-1">{c.title}</span>
                <span className="block text-xs text-muted-foreground">
                  {c.status === "handed_off" && c.ticket
                    ? `Request #${c.ticket.number}`
                    : c.status === "resolved"
                      ? "Answered"
                      : "Open"}{" "}
                  · {timeAgo(c.updated_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

/**
 * The customer assistant: answers from the organization's public help articles, with
 * sources, and a way to a person whenever it can't answer reliably.
 */
export function CustomerAssistant() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [pending, setPending] = useState<{ question: string; startedAt: number } | null>(null);
  const [draft, setDraft] = useState("");
  // "Talk to a person" opens the contact form for the conversation it was clicked in.
  const [personFor, setPersonFor] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const header = tenant(organization.id);

  const conversation = useQuery({
    queryKey: queryKeys.assistantConversation(organization.id, conversationId ?? "none"),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/assistant/conversations/{conversation_id}", {
          params: { path: { conversation_id: conversationId ?? "" }, header },
        }),
      ),
    enabled: conversationId !== null,
  });
  const current = conversationId ? conversation.data : undefined;
  const open = current?.status === "open";
  const showContact =
    !!current && !pending && open && (needsPerson(current) || personFor === current.id);
  const setWantsPerson = (wants: boolean) => setPersonFor(wants && current ? current.id : null);

  const store = async (updated: AssistantConversation) => {
    queryClient.setQueryData(queryKeys.assistantConversation(organization.id, updated.id), updated);
    setConversationId(updated.id);
    await queryClient.invalidateQueries({
      queryKey: queryKeys.assistantConversations(organization.id),
      refetchType: "all",
    });
  };

  const ask = useMutation({
    mutationFn: (question: string) =>
      unwrap(
        api.POST("/api/v1/assistant/questions", {
          params: { header },
          // A closed conversation can't take more questions: start a new one.
          body: { question, conversation_id: open ? conversationId : null },
        }),
      ),
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [current?.messages.length, pending]);

  const question = draft.trim();
  const canAsk = question.length >= 2 && !ask.isPending;

  async function submit() {
    if (!canAsk) return;
    setPending({ question, startedAt: Date.now() });
    setDraft("");
    try {
      await store(await ask.mutateAsync(question));
    } catch (error) {
      toast.error(errorMessage(error));
      setDraft(question);
    } finally {
      setPending(null);
    }
  }

  const startOver = () => {
    setConversationId(null);
    setDraft("");
  };

  if (organization.role !== "customer") {
    return (
      <div className="mx-auto max-w-md p-6">
        <Alert>
          <MessageSquareTextIcon aria-hidden />
          <AlertTitle>The customer assistant</AlertTitle>
          <AlertDescription>
            Customers ask questions here. As a member of the support team, use{" "}
            <Link href="/ask" className="underline">
              Ask AI
            </Link>{" "}
            instead.
          </AlertDescription>
        </Alert>
      </div>
    );
  }
  if (organization.settings.self_service !== "on") {
    return (
      <Empty className="mx-auto max-w-lg border py-12">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <MessageSquareTextIcon aria-hidden />
          </EmptyMedia>
          <EmptyTitle>The assistant isn&apos;t available</EmptyTitle>
          <EmptyDescription>Open a request and our support team will help you.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button render={<Link href="/tickets" />}>My requests</Button>
        </EmptyContent>
      </Empty>
    );
  }

  const messages = current?.messages ?? [];

  return (
    <div className="mx-auto grid w-full max-w-5xl items-start gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]">
      <div className="flex min-w-0 flex-col">
        <PageHeader
          title="Ask a question"
          description={`Answers come from ${organization.name}'s help articles, with their sources. If the assistant can't answer, our support team can.`}
          actions={
            conversationId ? (
              <Button variant="outline" onClick={startOver} disabled={ask.isPending}>
                <SquarePenIcon aria-hidden />
                New question
              </Button>
            ) : null
          }
        />

        {conversationId && conversation.isPending ? (
          <Skeleton className="mb-4 h-32 w-full" />
        ) : conversationId && conversation.isError ? (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{errorMessage(conversation.error)}</AlertDescription>
          </Alert>
        ) : messages.length === 0 && !pending ? (
          <Empty className="mb-4 border py-12">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <MessageSquareTextIcon aria-hidden />
              </EmptyMedia>
              <EmptyTitle>How can we help?</EmptyTitle>
              <EmptyDescription>
                Ask about setting up a product, an error message, returns, or your plan.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ol className="mb-4 space-y-4" aria-label="Conversation">
            {messages.map((message) =>
              message.role === "customer" ? (
                <li key={message.id} className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
                    {message.body}
                  </p>
                </li>
              ) : (
                <li key={message.id} className="rounded-xl border bg-card p-4 shadow-xs">
                  {current ? <Reply conversation={current} message={message} /> : null}
                </li>
              ),
            )}
            {pending ? (
              <>
                <li className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm whitespace-pre-wrap text-primary-foreground">
                    {pending.question}
                  </p>
                </li>
                <li className="rounded-xl border bg-card p-4 shadow-xs">
                  <Thinking since={pending.startedAt} />
                </li>
              </>
            ) : null}
          </ol>
        )}

        {current && showContact ? (
          <div className="mb-4">
            <ContactSupport conversation={current} onSent={(updated) => void store(updated)} />
          </div>
        ) : null}
        {current?.status === "handed_off" && current.ticket ? (
          <Alert className="mb-4">
            <HeadsetIcon aria-hidden />
            <AlertTitle>Sent to our support team</AlertTitle>
            <AlertDescription>
              We opened{" "}
              <Link href={`/tickets/${current.ticket.id}`} className="font-medium underline">
                request #{current.ticket.number}
              </Link>
              . We&apos;ll reply there.
            </AlertDescription>
          </Alert>
        ) : null}
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
            placeholder={current && !open ? "Ask another question…" : "Ask a question…"}
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
            {current && open && !showContact ? (
              <Button
                type="button"
                variant="link"
                size="xs"
                className="h-auto p-0 text-muted-foreground"
                onClick={() => setWantsPerson(true)}
              >
                Talk to a person
              </Button>
            ) : (
              <span />
            )}
            <Button type="submit" disabled={!canAsk}>
              {ask.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <ArrowUpIcon aria-hidden />}
              Ask
            </Button>
          </div>
        </form>
      </div>
      <aside className="space-y-4 lg:pt-16">
        <RecentConversations activeId={conversationId} onOpen={setConversationId} />
        <p className="px-1 text-xs text-muted-foreground">
          Prefer to write to us directly?{" "}
          <Link href="/tickets" className="underline">
            Open a request
          </Link>
          .
        </p>
      </aside>
    </div>
  );
}
