"use client";

import type { Schemas } from "@/lib/api/client";

type Compliance = Schemas["SlaCompliance"];

// Outcome states, always labelled in text; the bar only shows proportions.
const SEGMENTS: { key: "on_time" | "late" | "overdue"; label: string; className: string }[] = [
  { key: "on_time", label: "On time", className: "bg-emerald-600 dark:bg-emerald-500" },
  { key: "late", label: "Met late", className: "bg-amber-500" },
  { key: "overdue", label: "Overdue", className: "bg-destructive" },
];

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function Row({ label, data }: { label: string; data: Compliance }) {
  const decided = data.on_time + data.late + data.overdue;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-sm tabular-nums">
          <span className="text-lg font-semibold">{percent(data.rate)}</span>{" "}
          <span className="text-muted-foreground">on time{decided ? ` (${data.on_time} of ${decided})` : ""}</span>
        </span>
      </div>
      {decided ? (
        <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full bg-muted" aria-hidden>
          {SEGMENTS.filter((s) => data[s.key] > 0).map((s) => (
            <div key={s.key} className={s.className} style={{ width: `${(data[s.key] / decided) * 100}%` }} />
          ))}
        </div>
      ) : null}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {SEGMENTS.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className={`inline-block size-2 rounded-sm ${s.className}`} aria-hidden />
            {s.label}: <span className="tabular-nums text-foreground">{data[s.key]}</span>
          </li>
        ))}
        <li>
          Still in time: <span className="tabular-nums text-foreground">{data.open}</span>
        </li>
        <li>
          Cancelled: <span className="tabular-nums text-foreground">{data.cancelled}</span>
        </li>
      </ul>
    </div>
  );
}

/** First-response and resolution compliance for clocks started in the period. */
export function SlaCompliance({ sla }: { sla: Schemas["SlaSummary"] }) {
  return (
    <div className="space-y-5">
      <Row label="First response" data={sla.first_response} />
      <Row label="Resolution" data={sla.resolution} />
      <p className="text-xs text-muted-foreground">
        The rate counts tickets with an outcome: met on time, met late, or past the deadline
        unmet. Tickets still within their deadline and cancelled ones are listed but not counted.
        {sla.reopened_cycles
          ? ` ${sla.reopened_cycles} reopened resolution cycle${sla.reopened_cycles === 1 ? "" : "s"} are not included.`
          : ""}
      </p>
    </div>
  );
}
