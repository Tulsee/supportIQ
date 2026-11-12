"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Schemas } from "@/lib/api/client";
import { percent } from "@/lib/triage";

type Triage = Schemas["TriageAnalytics"];

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

/** AI triage in the period: how often it was sure, and how often people agreed. */
export function TriageStats({ triage, days }: { triage: Triage; days: number }) {
  return (
    <section aria-label="AI triage" className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold">AI triage</h2>
        <p className="text-sm text-muted-foreground">
          {triage.mode === "shadow"
            ? "Shadow mode: nothing is applied. Accuracy compares the AI's guesses with the categories people chose."
            : "Accuracy counts the predictions a person reviewed: kept or accepted is right, changed or removed is wrong."}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Sure enough to set"
          value={percent(triage.coverage)}
          hint={`${triage.confident} of ${triage.predictions} new tickets in ${days} days`}
        />
        <Stat
          label="Right when sure"
          value={percent(triage.accuracy)}
          hint={`${triage.confident_correct} of ${triage.confident_reviewed} reviewed`}
        />
        <Stat
          label="Right when unsure"
          value={percent(triage.unsure_accuracy)}
          hint={`the guess it held back: ${triage.unsure_correct} of ${triage.unsure_reviewed} reviewed`}
        />
        <Stat
          label="Time per ticket"
          value={triage.median_latency_ms == null ? "—" : `${(triage.median_latency_ms / 1000).toFixed(1)} s`}
          hint={triage.failed ? `${triage.failed} couldn't be triaged` : "median, in the background"}
        />
      </div>
      {triage.by_category.length ? (
        <Card>
          <CardHeader>
            <CardTitle>
              <h3>By category</h3>
            </CardTitle>
            <CardDescription>Confident predictions, and how many people kept.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Predicted</TableHead>
                  <TableHead className="text-right">Reviewed</TableHead>
                  <TableHead className="text-right">Right</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {triage.by_category.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell>{row.name}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.predicted}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.reviewed}</TableCell>
                    <TableCell className="text-right tabular-nums">{percent(row.accuracy)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}
