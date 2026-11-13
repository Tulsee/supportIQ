"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import {
  CategoryBars,
  DailyLines,
  DailyStackedBars,
  DataTable,
  type Series,
} from "@/components/analytics/charts";
import { RoutingStats } from "@/components/analytics/routing-stats";
import { SelfServiceStats } from "@/components/analytics/self-service-stats";
import { SlaCompliance } from "@/components/analytics/sla-compliance";
import { SlaDetailView } from "@/components/analytics/sla-detail";
import { TriageStats } from "@/components/analytics/triage-stats";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { ANSWER_STATUS, DECISION_LABELS, DECISION_REASONS, formatSeconds } from "@/lib/answers";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { timeAgo } from "@/lib/tickets";

const PERIODS = [7, 30, 90] as const;

const TICKET_SERIES: Series[] = [
  { key: "created", label: "Opened", color: "var(--viz-1)" },
  { key: "resolved", label: "Resolved", color: "var(--viz-2)" },
];
const RUN_SERIES: Series[] = [
  { key: "answer", label: "Questions", color: "var(--viz-1)" },
  { key: "suggestion", label: "Reply suggestions", color: "var(--viz-2)" },
  { key: "summary", label: "Summaries", color: "var(--viz-3)" },
  { key: "retrieval", label: "Searches", color: "var(--viz-4)" },
];
const KIND_LABELS: Record<string, string> = {
  ...Object.fromEntries(RUN_SERIES.map((s) => [s.key, s.label])),
  self_service: "Customer questions",
};

function duration(minutes: number | null | undefined): string {
  if (minutes == null) return "—";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)} h`;
  return `${(minutes / 1440).toFixed(1)} days`;
}

function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card size="sm">
      <CardContent className="space-y-1">
        <div className="text-xs text-muted-foreground" title={hint}>
          {label}
        </div>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

function ChartCard({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>{title}</h2>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

/** Support operations and AI performance over a trailing window. */
export function AnalyticsPage() {
  const organization = useActiveOrganization();
  const [days, setDays] = useState<number>(30);
  const header = { params: { query: { days }, header: tenant(organization.id) } };
  const overview = useQuery({
    queryKey: queryKeys.analytics(organization.id, "overview", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/overview", header)),
    placeholderData: keepPreviousData,
  });
  const slaDetail = useQuery({
    queryKey: queryKeys.analytics(organization.id, "sla", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/sla", header)),
    placeholderData: keepPreviousData,
  });
  const triage = useQuery({
    queryKey: queryKeys.analytics(organization.id, "triage", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/triage", header)),
    placeholderData: keepPreviousData,
  });
  const routing = useQuery({
    queryKey: queryKeys.analytics(organization.id, "routing", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/routing", header)),
    placeholderData: keepPreviousData,
  });
  const selfService = useQuery({
    queryKey: queryKeys.analytics(organization.id, "self-service", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/self-service", header)),
    placeholderData: keepPreviousData,
  });
  const ai = useQuery({
    queryKey: queryKeys.analytics(organization.id, "ai", days),
    queryFn: () => unwrap(api.GET("/api/v1/analytics/ai-performance", header)),
    placeholderData: keepPreviousData,
  });

  const error = overview.error ?? ai.error ?? routing.error;
  // Shown once routing is in use (or was, in the period).
  const routingInUse =
    routing.data &&
    (routing.data.mode !== "off" ||
      routing.data.decisions.total + routing.data.decisions.shadow_total > 0);
  return (
    <>
      <PageHeader
        title="Analytics"
        description="Support operations and how the AI assistant is doing."
        actions={
          <Tabs value={String(days)} onValueChange={(value) => setDays(Number(value))}>
            <TabsList aria-label="Period">
              {PERIODS.map((p) => (
                <TabsTrigger key={p} value={String(p)}>
                  {p} days
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        }
      />
      {error ? (
        <Alert variant="destructive" className="mb-6">
          <AlertDescription>{errorMessage(error)}</AlertDescription>
        </Alert>
      ) : null}
      {!overview.data || !ai.data ? (
        <div className="space-y-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : (
        <div className="space-y-6" aria-busy={overview.isFetching || ai.isFetching}>
          <section aria-label="Support" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Open tickets" value={overview.data.tickets.open} hint={`${overview.data.tickets.unassigned} unassigned`} />
            <Stat label={`Opened in ${days} days`} value={overview.data.tickets.created} hint={`${overview.data.tickets.resolved} resolved`} />
            <Stat label="Median first response" value={duration(overview.data.median_first_response_minutes)} hint={`${overview.data.tickets_replied} tickets replied to`} />
            <Stat label="Median time to resolve" value={duration(overview.data.median_resolution_minutes)} />
          </section>

          {overview.data.sla.configured ? (
            <ChartCard
              title="Service levels"
              description={`First-response and resolution targets for tickets opened in the last ${days} days.`}
            >
              <SlaCompliance sla={overview.data.sla} />
              {slaDetail.data ? <SlaDetailView detail={slaDetail.data} /> : null}
            </ChartCard>
          ) : null}

          {triage.data && (triage.data.mode !== "off" || triage.data.predictions > 0) ? (
            <TriageStats triage={triage.data} days={days} />
          ) : null}
          {routingInUse ? <RoutingStats routing={routing.data!} days={days} /> : null}
          {selfService.data && (selfService.data.mode === "on" || selfService.data.conversations > 0) ? (
            <SelfServiceStats stats={selfService.data} days={days} />
          ) : null}

          <ChartCard title="Tickets per day" description="Opened and resolved, by UTC day.">
            <DailyLines rows={overview.data.daily} series={TICKET_SERIES} label="Tickets opened and resolved per day" />
            <DataTable rows={overview.data.daily} series={TICKET_SERIES} caption="Tickets opened and resolved per day" />
          </ChartCard>

          <section aria-label="AI" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="AI questions and suggestions" value={ai.data.answers.total} />
            <Stat
              label="Answered"
              value={percent(ai.data.answers.answer_rate)}
              hint="with sources from the knowledge base"
            />
            <Stat
              label="Handed to a person"
              value={percent(ai.data.answers.human_review_rate)}
              hint="escalated, held back or failed"
            />
            <Stat
              label="Rated helpful"
              value={percent(ai.data.feedback.helpful_rate)}
              hint={`${ai.data.feedback.helpful + ai.data.feedback.not_helpful} ratings`}
            />
          </section>

          <ChartCard title="AI activity per day" description="Runs by kind: questions, reply suggestions, summaries and searches.">
            <DailyStackedBars rows={ai.data.daily_runs} series={RUN_SERIES} label="AI runs per day by kind" />
            <DataTable rows={ai.data.daily_runs} series={RUN_SERIES} caption="AI runs per day by kind" />
          </ChartCard>

          <div className="grid gap-6 lg:grid-cols-2">
            <ChartCard title="Answer outcomes" description="What the assistant did with questions and reply suggestions.">
              {ai.data.answers.total ? (
                <CategoryBars
                  items={Object.entries(ai.data.answers.by_status)
                    .map(([status, value]) => ({
                      label: ANSWER_STATUS[status as keyof typeof ANSWER_STATUS]?.label ?? status,
                      value,
                      hint: ANSWER_STATUS[status as keyof typeof ANSWER_STATUS]?.description,
                    }))
                    .sort((a, b) => b.value - a.value)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">No questions in this period.</p>
              )}
            </ChartCard>

            <ChartCard title="Why" description="The evidence policy's decisions and their reasons.">
              {ai.data.decisions.length ? (
                <CategoryBars
                  items={ai.data.decisions.map((d) => ({
                    label: `${DECISION_LABELS[d.decision] ?? d.decision}: ${DECISION_REASONS[d.reason]?.replace(/\.$/, "") ?? d.reason}`,
                    value: d.count,
                  }))}
                />
              ) : (
                <p className="text-sm text-muted-foreground">No assessed answers in this period.</p>
              )}
            </ChartCard>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <ChartCard title="Response time" description="How long AI runs take, median and 95th percentile.">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Kind</TableHead>
                    <TableHead className="text-right">Runs</TableHead>
                    <TableHead className="text-right">Median</TableHead>
                    <TableHead className="text-right">95th pct.</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(ai.data.latency).map(([kind, stat]) => (
                    <TableRow key={kind}>
                      <TableCell>{KIND_LABELS[kind] ?? kind}</TableCell>
                      <TableCell className="text-right tabular-nums">{stat.runs}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatSeconds(stat.p50_ms)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatSeconds(stat.p95_ms)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {ai.data.models.length ? (
                <p className="text-xs text-muted-foreground">
                  {ai.data.models
                    .map((m) => `${m.model} (${m.provider}): ${m.runs} runs, ${m.input_tokens.toLocaleString()} tokens in, ${m.output_tokens.toLocaleString()} out`)
                    .join(" · ")}
                </p>
              ) : null}
            </ChartCard>

            <ChartCard title="Knowledge gaps" description="Questions most often not answered confidently: what the documents are missing or get wrong.">
              {ai.data.knowledge_gaps.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Question</TableHead>
                      <TableHead className="text-right">Times</TableHead>
                      <TableHead className="hidden sm:table-cell">Last</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {ai.data.knowledge_gaps.map((gap) => (
                      <TableRow key={gap.query}>
                        <TableCell className="max-w-0 w-full">
                          <Link href={`/ai-inspector/${gap.run_id}`} className="block truncate hover:underline" title={gap.query}>
                            {gap.query}
                          </Link>
                          <div className="text-xs text-muted-foreground">
                            {ANSWER_STATUS[gap.status as keyof typeof ANSWER_STATUS]?.label ?? gap.status}
                          </div>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{gap.count}</TableCell>
                        <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">
                          {timeAgo(gap.last_asked)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">None in this period.</p>
              )}
            </ChartCard>
          </div>
        </div>
      )}
    </>
  );
}
