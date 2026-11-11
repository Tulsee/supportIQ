import { TriangleAlertIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import type { AIRun } from "@/lib/api/client";
import {
  QUERY_TYPE_LABELS,
  STAGE_LABELS,
  STRATEGY_LABELS,
  formatMs,
  stageTimings,
} from "@/lib/retrieval";

/** How a run was planned and how long each stage took. */
export function RunSummary({ run }: { run: AIRun }) {
  const weights = run.config.weights as { sparse: number; dense: number } | undefined;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="secondary">{QUERY_TYPE_LABELS[run.query_type] ?? run.query_type}</Badge>
        <span className="text-muted-foreground">→</span>
        <Badge>{STRATEGY_LABELS[run.strategy] ?? run.strategy}</Badge>
        {run.requested_strategy !== "auto" ? (
          <span className="text-xs text-muted-foreground">(fixed strategy)</span>
        ) : null}
        {weights ? (
          <span className="text-xs text-muted-foreground tabular-nums">
            weights BM25 {weights.sparse} · semantic {weights.dense}
          </span>
        ) : null}
      </div>
      {run.reasons.length ? (
        <ul className="list-inside list-disc text-sm text-muted-foreground">
          {run.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
        {stageTimings(run).map(([stage, ms]) => (
          <span key={stage} className={stage === "total" ? "font-medium text-foreground" : ""}>
            {STAGE_LABELS[stage] ?? stage} {formatMs(ms)}
          </span>
        ))}
      </div>
      {Object.keys(run.degraded).length ? (
        <Alert>
          <TriangleAlertIcon aria-hidden />
          <AlertTitle>Ran without {Object.keys(run.degraded).map((s) => STAGE_LABELS[s] ?? s).join(", ")}</AlertTitle>
          <AlertDescription>
            A retriever failed, so these results come from the others only.
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}
