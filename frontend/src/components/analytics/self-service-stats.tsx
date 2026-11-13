"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";

import { CategoryBars, DailyStackedBars, DataTable, type Series } from "@/components/analytics/charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Schemas } from "@/lib/api/client";
import { DECISION_REASONS } from "@/lib/answers";
import { timeAgo } from "@/lib/tickets";
import { percent } from "@/lib/triage";

type SelfService = Schemas["SelfServiceAnalytics"];

// How each day's conversations ended, in the palette's fixed order.
const OUTCOME_SERIES: Series[] = [
  { key: "resolved", label: "Resolved", color: "var(--viz-1)" },
  { key: "handed_off", label: "Handed to a person", color: "var(--viz-2)" },
  { key: "open", label: "No confirmation", color: "var(--viz-3)" },
];

const REASONS: Record<string, string> = {
  ...DECISION_REASONS,
  unavailable: "Search was unavailable.",
};

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Card size="sm">
      <CardContent className="space-y-1">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground">{hint}</div>
      </CardContent>
    </Card>
  );
}

/** The customer assistant in the period: how conversations ended and what it missed. */
export function SelfServiceStats({ stats, days }: { stats: SelfService; days: number }) {
  const daily = stats.daily.map((d) => ({
    day: d.day,
    resolved: d.resolved,
    handed_off: d.handed_off,
    open: d.conversations - d.resolved - d.handed_off,
  }));
  return (
    <section aria-label="Customer assistant" className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold">Customer assistant</h2>
        <p className="text-sm text-muted-foreground">
          Conversations customers started in the portal. A conversation counts as resolved only when
          the customer said an answer helped.
          {stats.mode === "off" ? " The assistant is off now." : ""}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Resolved by the assistant"
          value={percent(stats.resolution_rate)}
          hint={`${stats.resolved} of ${stats.conversations} conversations in ${days} days`}
        />
        <Stat
          label="Handed to a person"
          value={percent(stats.handoff_rate)}
          hint={`${stats.handed_off} of ${stats.conversations} became requests`}
        />
        <Stat
          label="Questions answered"
          value={percent(stats.answer_rate)}
          hint={`${stats.answered} of ${stats.replies}; ${stats.clarify} asked to clarify`}
        />
        <Stat
          label="Rated helpful"
          value={percent(stats.helpful_rate)}
          hint={`${stats.helpful} of ${stats.rated} rated answers`}
        />
      </div>
      {stats.conversations ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h3>Conversations per day</h3>
            </CardTitle>
            <CardDescription>By the day they started (UTC), and how they ended.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <DailyStackedBars rows={daily} series={OUTCOME_SERIES} label="Assistant conversations per day by outcome" />
            <DataTable rows={daily} series={OUTCOME_SERIES} caption="Assistant conversations per day by outcome" />
          </CardContent>
        </Card>
      ) : null}
      {stats.unable ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>
                <h3>Why it couldn&apos;t answer</h3>
              </CardTitle>
              <CardDescription>
                {stats.unable} {stats.unable === 1 ? "reply" : "replies"} offered a person instead.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <CategoryBars
                items={stats.reasons.map((r) => ({
                  label: REASONS[r.reason]?.replace(/\.$/, "") ?? r.reason,
                  value: r.replies,
                }))}
              />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>
                <h3>Questions it missed</h3>
              </CardTitle>
              <CardDescription>The latest ones: candidates for a new public article.</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y text-sm">
                {stats.unanswered.map((q) => (
                  <li key={`${q.conversation_id}-${q.asked_at}`} className="space-y-0.5 py-2 first:pt-0">
                    <p className="line-clamp-2">{q.question}</p>
                    <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                      <span>{timeAgo(q.asked_at)}</span>
                      {q.ticket_id ? (
                        <Link href={`/tickets/${q.ticket_id}`} className="hover:underline">
                          Became a request
                        </Link>
                      ) : null}
                      {q.ai_run_id ? (
                        <Link
                          href={`/ai-inspector/${q.ai_run_id}`}
                          className="ml-auto inline-flex items-center gap-0.5 font-medium text-foreground hover:underline"
                        >
                          Inspect run
                          <ChevronRightIcon className="size-3.5" aria-hidden />
                        </Link>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </section>
  );
}
