"use client";

import { useState } from "react";

import { DailyStackedBars, DataTable, type Series } from "@/components/analytics/charts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Schemas } from "@/lib/api/client";
import { formatMinutes } from "@/lib/sla";
import { PRIORITY_LABELS } from "@/lib/tickets";

type Detail = Schemas["SlaDetail"];
type Compliance = Schemas["SlaCompliance"];
type Group = "by_priority" | "by_team" | "by_agent";

const GROUPS: { value: Group; label: string }[] = [
  { value: "by_priority", label: "Priority" },
  { value: "by_team", label: "Team" },
  { value: "by_agent", label: "Assignee" },
];
const DAILY_SERIES: Series[] = [
  { key: "at_risk", label: "Flagged due soon", color: "var(--viz-4)" },
  { key: "breached", label: "Breached", color: "var(--viz-2)" },
];
const PAUSE_REASONS: Record<string, string> = { waiting_for_customer: "Waiting for the customer" };

function rate(data: Compliance): string {
  const decided = data.on_time + data.late + data.overdue;
  if (!decided) return "—";
  return `${Math.round((data.rate ?? 0) * 100)}% (${data.on_time} of ${decided})`;
}

function businessTime(minutes: number | null | undefined): string {
  return minutes == null ? "—" : formatMinutes(Math.round(minutes));
}

/** Service levels in depth: by priority, team and assignee; business-time medians;
 * due-soon and breached per day; pause reasons. */
export function SlaDetailView({ detail }: { detail: Detail }) {
  const [group, setGroup] = useState<Group>("by_priority");
  const rows = detail[group];
  const anyDaily = detail.daily.some((d) => d.at_risk || d.breached);

  return (
    <div className="space-y-6 border-t pt-5">
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Median business time to first response</dt>
          <dd className="text-lg font-semibold tabular-nums">
            {businessTime(detail.median_business_minutes.first_response)}
          </dd>
          <dd className="text-xs text-muted-foreground">over {detail.met.first_response} met</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Median business time to resolution</dt>
          <dd className="text-lg font-semibold tabular-nums">
            {businessTime(detail.median_business_minutes.resolution)}
          </dd>
          <dd className="text-xs text-muted-foreground">
            over {detail.met.resolution} met, waiting for the customer excluded
          </dd>
        </div>
      </dl>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">On time, by</h3>
          <Tabs value={group} onValueChange={(value) => setGroup(value as Group)}>
            <TabsList aria-label="Group service levels by">
              {GROUPS.map((g) => (
                <TabsTrigger key={g.value} value={g.value}>
                  {g.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
        {rows.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{GROUPS.find((g) => g.value === group)?.label}</TableHead>
                <TableHead className="text-right">First response</TableHead>
                <TableHead className="text-right">Resolution</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key ?? "none"}>
                  <TableCell>
                    {group === "by_priority"
                      ? (PRIORITY_LABELS[row.label as keyof typeof PRIORITY_LABELS] ?? row.label)
                      : row.label}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{rate(row.first_response)}</TableCell>
                  <TableCell className="text-right tabular-nums">{rate(row.resolution)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">No SLA clocks in this period.</p>
        )}
        <p className="text-xs text-muted-foreground">
          Grouped by each ticket&apos;s priority, team and assignee as they are now.
        </p>
      </div>

      {anyDaily ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Flagged due soon and breached, per day</h3>
          <DailyStackedBars rows={detail.daily} series={DAILY_SERIES} label="SLA clocks flagged due soon and breached per day" />
          <DataTable rows={detail.daily} series={DAILY_SERIES} caption="SLA clocks flagged due soon and breached per day" />
        </div>
      ) : null}

      {detail.pauses.length ? (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Why clocks were paused</h3>
          <ul className="space-y-1 text-sm">
            {detail.pauses.map((pause) => (
              <li key={pause.reason} className="flex flex-wrap justify-between gap-2">
                <span>{PAUSE_REASONS[pause.reason] ?? pause.reason}</span>
                <span className="text-muted-foreground tabular-nums">
                  {pause.count} {pause.count === 1 ? "pause" : "pauses"}
                  {pause.ongoing ? `, ${pause.ongoing} ongoing` : ""}
                  {pause.median_minutes != null ? ` · median ${formatMinutes(Math.round(pause.median_minutes))}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
