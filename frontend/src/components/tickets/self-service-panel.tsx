"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDownIcon, ChevronRightIcon, MessageSquareTextIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  api,
  tenant,
  unwrap,
  type StaffAssistantMessage,
  type Ticket,
} from "@/lib/api/client";
import { ANSWER_STATUS, DECISION_REASONS } from "@/lib/answers";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

const OUTCOMES = {
  answered: { label: "Answered", variant: "secondary" },
  clarify: { label: "Asked to clarify", variant: "outline" },
  unable: { label: "Couldn't answer", variant: "destructive" },
} as const;

function why(message: StaffAssistantMessage): string | null {
  const run = message.run;
  if (!run) return message.outcome === "unable" ? "Search was unavailable." : null;
  if (run.reason && run.reason !== "supported") return DECISION_REASONS[run.reason] ?? run.reason;
  if (run.answer_status && run.answer_status !== "answered") {
    return ANSWER_STATUS[run.answer_status].description;
  }
  return null;
}

function Reply({ message }: { message: StaffAssistantMessage }) {
  const outcome = message.outcome ? OUTCOMES[message.outcome] : null;
  const reason = why(message);
  return (
    <div className="space-y-1 rounded-lg border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {outcome ? <Badge variant={outcome.variant}>{outcome.label}</Badge> : null}
        {message.rating ? (
          <span className="text-muted-foreground">
            Customer: {message.rating === "helpful" ? "it helped" : "it didn't help"}
          </span>
        ) : null}
        {message.run ? (
          <Link
            href={`/ai-inspector/${message.run.ai_run_id}`}
            className="ml-auto inline-flex items-center gap-0.5 font-medium hover:underline"
          >
            Inspect run
            <ChevronRightIcon className="size-3.5" aria-hidden />
          </Link>
        ) : null}
      </div>
      {message.outcome !== "unable" ? (
        <p className="text-sm whitespace-pre-wrap">{message.body}</p>
      ) : null}
      {reason ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
    </div>
  );
}

/** Staff: the conversation with the customer assistant this request was opened from. */
export function SelfServicePanel({ ticket }: { ticket: Ticket }) {
  const organization = useActiveOrganization();
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: queryKeys.ticketSelfService(organization.id, ticket.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/self-service", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
        }),
      ),
    staleTime: 60_000,
  });
  const conversation = query.data;
  if (!conversation) return null;

  const replies = conversation.messages.filter((m) => m.role === "assistant");
  const questions = conversation.messages.length - replies.length;
  const unable = replies.filter((m) => m.outcome === "unable").length;

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-1.5">
            <MessageSquareTextIcon className="size-4 text-muted-foreground" aria-hidden />
            Asked the assistant first
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {questions} question{questions === 1 ? "" : "s"}
          {unable ? `; it couldn't answer ${unable === replies.length ? "any" : unable}` : ""}. Only
          you can see this.
        </p>
        <Button
          variant="link"
          size="xs"
          className="h-auto p-0"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? (
            <ChevronDownIcon aria-hidden />
          ) : (
            <ChevronRightIcon aria-hidden />
          )}
          {expanded ? "Hide the conversation" : "Show the conversation"}
        </Button>
        {expanded ? (
          <ol className="space-y-2">
            {conversation.messages.map((message) => (
              <li key={message.id}>
                {message.role === "customer" ? (
                  <p className="text-sm">
                    <span className="font-medium">{conversation.customer_name ?? "Customer"}:</span>{" "}
                    {message.body}
                  </p>
                ) : (
                  <Reply message={message} />
                )}
              </li>
            ))}
          </ol>
        ) : null}
      </CardContent>
    </Card>
  );
}
