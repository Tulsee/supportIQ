"use client";

import { CategoryBars } from "@/components/analytics/charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Schemas } from "@/lib/api/client";
import { OUTCOMES } from "@/lib/routing";
import { cn } from "@/lib/utils";

type Routing = Schemas["RoutingAnalytics"];

function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function duration(minutes: number | null | undefined): string {
  if (minutes == null) return "—";
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)} h`;
  return `${(minutes / 1440).toFixed(1)} days`;
}

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

function outcomeItems(counts: Routing["decisions"]["by_outcome"]) {
  return (Object.keys(OUTCOMES) as (keyof typeof OUTCOMES)[]).map((outcome) => ({
    label: OUTCOMES[outcome].label,
    value: counts[outcome],
  }));
}

/** Automatic routing in the period: how much it did, how fast, and how people took it. */
export function RoutingStats({ routing, days }: { routing: Routing; days: number }) {
  const { assignment, overrides, shadow, decisions } = routing;
  return (
    <section aria-label="Routing" className="space-y-3">
      <h2 className="text-lg font-semibold">Routing</h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Assigned automatically"
          value={percent(assignment.automatic_rate)}
          hint={`${assignment.automatic} of ${assignment.tickets_assigned} tickets opened in ${days} days`}
        />
        <Stat
          label="Median time to assignment"
          value={duration(assignment.median_minutes)}
          hint={`routing ${duration(assignment.median_minutes_automatic)} · people ${duration(assignment.median_minutes_manual)}`}
        />
        <Stat
          label="Changed by a person"
          value={percent(overrides.rate)}
          hint={`within an hour: ${overrides.changed_within_hour} of ${overrides.automatic} automatic assignments`}
        />
        {shadow.decisions ? (
          <Stat
            label="Agreed with shadow routing"
            value={percent(shadow.rate)}
            hint={`people picked the same agent ${shadow.agreed} of ${shadow.compared} times`}
          />
        ) : (
          <Stat
            label="Nobody available"
            value={String(decisions.by_outcome.no_agent)}
            hint="tickets routing couldn't hand to anyone"
          />
        )}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <h3>Routing outcomes</h3>
            </CardTitle>
            <CardDescription>
              {decisions.total
                ? `${decisions.total} decisions carried out in the last ${days} days, reroutes included.`
                : `No decisions carried out in the last ${days} days.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {decisions.total ? <CategoryBars items={outcomeItems(decisions.by_outcome)} /> : null}
            {decisions.shadow_total ? (
              <div className="space-y-2">
                <p className="text-sm text-muted-foreground">
                  In shadow mode: {decisions.shadow_total} decisions recorded but not carried out.
                </p>
                <CategoryBars items={outcomeItems(decisions.shadow_by_outcome)} />
              </div>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>
              <h3>Workload</h3>
            </CardTitle>
            <CardDescription>
              Tickets each person works on now (assigned or in progress), and how they got
              assignments in the last {days} days.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className="w-32">Now</TableHead>
                  <TableHead className="text-right">By routing</TableHead>
                  <TableHead className="text-right">By people</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {routing.workload.map((agent) => {
                  const capacity = agent.max_active_tickets;
                  const full = capacity === 0 || agent.active_tickets >= capacity;
                  return (
                    <TableRow key={agent.user_id}>
                      <TableCell className="whitespace-normal">{agent.name}</TableCell>
                      <TableCell>
                        <div className="space-y-1">
                          <div className="text-sm tabular-nums">
                            {agent.active_tickets} <span className="text-muted-foreground">of {capacity}</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                            <div
                              className={cn("h-full rounded-full", full ? "bg-amber-500" : "bg-primary")}
                              style={{
                                width: `${capacity ? Math.min(agent.active_tickets / capacity, 1) * 100 : 100}%`,
                              }}
                            />
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{agent.assigned_automatically}</TableCell>
                      <TableCell className="text-right tabular-nums">{agent.assigned_by_people}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
