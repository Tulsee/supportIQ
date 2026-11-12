"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ChevronDownIcon, SparklesIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { refreshTickets } from "@/components/tickets/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage, tenant, unwrap, type Ticket, type TicketPriority, type TicketTriage } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { PRIORITY_LABELS } from "@/lib/tickets";
import { languageName, percent } from "@/lib/triage";

const CONFIDENCE_WORDS: Record<string, string> = { "0.9": "high", "0.65": "medium", "0.35": "low" };

/** The combined score, not a probability (it isn't calibrated: see the T1-T5 report). */
function score(value: number): string {
  return String(Number(value.toFixed(3))); // 0.419 vs a 0.425 threshold: don't round both to 0.42
}

function Review({ triage }: { triage: TicketTriage }) {
  const review = triage.review;
  if (!review) return null;
  const who = review.reviewed_by?.full_name ?? "Someone";
  const text =
    review.outcome === "confirmed"
      ? `${who} confirmed it`
      : review.outcome === "cleared"
        ? `${who} removed the category`
        : `${who} chose ${review.final_category_key} instead`;
  return <p className="text-xs text-muted-foreground">{text}</p>;
}

function Details({ triage }: { triage: TicketTriage }) {
  const [open, setOpen] = useState(false);
  const llm = triage.llm;
  const neighbours = triage.neighbours;
  if (!llm && !neighbours) return null;
  return (
    <div className="space-y-2">
      <Button
        variant="link"
        size="xs"
        className="h-auto p-0"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ChevronDownIcon className={open ? "rotate-180" : ""} aria-hidden />
        {open ? "Hide why" : "Why"}
      </Button>
      {open ? (
        <ul className="space-y-1.5 text-xs text-muted-foreground">
          <li>
            The language model{" "}
            {llm?.category
              ? `picked ${llm.category} (${CONFIDENCE_WORDS[String(llm.confidence)] ?? percent(llm.confidence)} confidence).`
              : llm
                ? "wasn't sure."
                : "couldn't be used."}
          </li>
          <li>
            {neighbours
              ? `Similar tickets your team sorted: ${Object.entries(neighbours.shares)
                  .sort((a, b) => b[1] - a[1])
                  .map(([key, share]) => `${key} ${percent(share)}`)
                  .join(", ")}.`
              : "No similar tickets your team sorted yet."}
          </li>
          {neighbours?.examples.length ? (
            <li className="flex flex-wrap gap-1.5">
              {neighbours.examples.slice(0, 6).map((example) => (
                <Link
                  key={example.ticket_id}
                  href={`/tickets/${example.ticket_id}`}
                  className="rounded border px-1.5 py-0.5 hover:bg-muted"
                  title={`${percent(example.similarity)} similar`}
                >
                  #{example.number ?? "?"} {example.category}
                </Link>
              ))}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/** Staff: what AI triage made of the ticket, with accept and confirm. Hidden in
 * shadow mode, so the prediction can't sway the person it's measured against. */
export function TriagePanel({ ticket }: { ticket: Ticket }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const params = { path: { ticket_id: ticket.id }, header: tenant(organization.id) };
  const query = useQuery({
    queryKey: queryKeys.ticketTriage(organization.id, ticket.id),
    queryFn: () => unwrap(api.GET("/api/v1/tickets/{ticket_id}/triage", { params })),
    staleTime: 0,
  });
  const refresh = () => refreshTickets(queryClient, organization.id);
  const accept = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/tickets/{ticket_id}/triage/accept", { params })),
    onSuccess: async (result) => {
      toast.success(result.applied ? "Thanks, confirmed" : `Category set to ${result.category?.name}`);
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const setPriority = useMutation({
    mutationFn: (priority: TicketPriority) =>
      unwrap(api.PATCH("/api/v1/tickets/{ticket_id}", { params, body: { priority } })),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error)),
  });

  const triage = query.data;
  if (!triage || triage.mode === "shadow") return null;
  const closed = ticket.status === "closed";
  const category = triage.category;
  const suggestedPriority = triage.priority as TicketPriority | null;
  const language = languageName(triage.language);

  let headline;
  if (triage.applied && category) {
    headline = (
      <p className="text-sm">
        Set the category to <span className="font-medium">{category.name}</span>{" "}
        <span className="text-muted-foreground">(score {score(triage.confidence)})</span>
      </p>
    );
  } else if (category) {
    headline = (
      <p className="text-sm">
        Suggests <span className="font-medium">{category.name}</span>{" "}
        <span className="text-muted-foreground">
          (score {score(triage.confidence)}; it sets the category from {score(triage.threshold)})
        </span>
      </p>
    );
  } else {
    headline = (
      <p className="text-sm text-muted-foreground">
        {triage.decision === "failed"
          ? `Couldn't triage this ticket${triage.error ? `: ${triage.error}` : "."}`
          : "Not sure what this ticket is about; a person should choose the category."}
      </p>
    );
  }

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>
          <h2 className="flex items-center gap-1.5">
            <SparklesIcon className="size-4 text-muted-foreground" aria-hidden />
            AI triage
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          {headline}
          {category && !triage.review && !closed ? (
            <Button
              size="sm"
              variant={triage.applied ? "outline" : "default"}
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              <CheckIcon aria-hidden />
              {triage.applied ? "Looks right" : `Use ${category.name}`}
            </Button>
          ) : null}
          <Review triage={triage} />
        </div>
        {suggestedPriority && suggestedPriority !== ticket.priority && !closed ? (
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>
              Suggests <span className="font-medium">{PRIORITY_LABELS[suggestedPriority]}</span> priority
            </span>
            <Button
              size="xs"
              variant="outline"
              disabled={setPriority.isPending}
              onClick={() => setPriority.mutate(suggestedPriority)}
            >
              Use it
            </Button>
          </div>
        ) : null}
        {language && triage.language !== "en" ? (
          <p className="text-sm">
            Written in <span className="font-medium">{language}</span>
          </p>
        ) : null}
        {triage.urgency_cues.length ? (
          <ul className="flex flex-wrap gap-1.5" aria-label="Signs of urgency">
            {triage.urgency_cues.map((cue) => (
              <li key={cue}>
                <Badge variant="outline" className="font-normal">
                  “{cue}”
                </Badge>
              </li>
            ))}
          </ul>
        ) : null}
        <Details triage={triage} />
      </CardContent>
    </Card>
  );
}
