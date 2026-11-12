import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Schemas } from "@/lib/api/client";
import { DECISION_LABELS, DECISION_REASONS, formatSeconds } from "@/lib/answers";
import { cn } from "@/lib/utils";

type Assessment = Schemas["AssessmentRead"];

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** A 0-1 signal as a bar, with the threshold the policy applied to it. */
function Signal({
  label,
  value,
  hint,
  limit,
  higherIsBetter = true,
}: {
  label: string;
  value: number | null | undefined;
  hint: string;
  limit?: { value: number; kind: "min" | "max" } | null;
  higherIsBetter?: boolean;
}) {
  const failed =
    value != null && limit
      ? limit.kind === "min"
        ? value < limit.value
        : value > limit.value
      : false;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span title={hint}>{label}</span>
        <span className={cn("tabular-nums", failed ? "font-medium text-destructive" : "text-muted-foreground")}>
          {value == null ? "not measured" : percent(value)}
          {limit ? (
            <span className="ml-1 text-xs text-muted-foreground">
              ({limit.kind} {percent(Math.min(limit.value, 1))})
            </span>
          ) : null}
        </span>
      </div>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        {value != null ? (
          <div
            className={cn(
              "h-full rounded-full",
              failed ? "bg-destructive" : higherIsBetter ? "bg-primary" : "bg-muted-foreground/60",
            )}
            style={{ width: `${Math.max(2, Math.round(value * 100))}%` }}
          />
        ) : null}
        {limit && limit.value <= 1 ? (
          <div className="absolute inset-y-0 w-px bg-foreground/60" style={{ left: `${limit.value * 100}%` }} />
        ) : null}
      </div>
    </div>
  );
}

/** What the evidence looked like, and why the assistant answered, asked, escalated or held back. */
export function AssessmentCard({ assessment }: { assessment: Assessment }) {
  const t = assessment.thresholds as {
    min_relevance?: number;
    min_coverage?: number;
    max_contradiction?: number;
    min_grounding?: number;
    clarify_vague?: boolean;
  };
  const signals = assessment.signals as { missing_terms?: string[]; model_status?: string | null };
  const missing = signals.missing_terms ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <h2>Evidence assessment</h2>
          <Badge variant={assessment.decision === "answer" ? "default" : "secondary"}>
            {DECISION_LABELS[assessment.decision] ?? assessment.decision}
          </Badge>
        </CardTitle>
        <CardDescription>
          {DECISION_REASONS[assessment.reason] ?? assessment.reason}
          {assessment.stage === "before" ? " The model wasn't asked." : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <Signal
            label="Relevance"
            value={assessment.relevance}
            hint="How well the best passage matches the question (cross-encoder)."
            limit={t.min_relevance ? { value: t.min_relevance, kind: "min" } : null}
          />
          <Signal
            label="Coverage"
            value={assessment.coverage}
            hint="Share of the question's words found in the passages."
            limit={t.min_coverage ? { value: t.min_coverage, kind: "min" } : null}
          />
          <Signal
            label="Consistency"
            value={assessment.consistency}
            hint="0 when the model reported that the passages disagree."
          />
          <Signal
            label="Grounding"
            value={assessment.grounding}
            hint="How strongly the cited passages state the answer's claims (NLI entailment)."
            limit={t.min_grounding ? { value: t.min_grounding, kind: "min" } : null}
          />
          <Signal
            label="Strongest contradiction"
            value={assessment.contradiction}
            hint="The most any cited passage contradicts a claim citing it (NLI)."
            limit={t.max_contradiction != null ? { value: t.max_contradiction, kind: "max" } : null}
            higherIsBetter={false}
          />
          <Signal
            label="Evidence score"
            value={assessment.score}
            hint="Weighted combination of the signals. It ranks evidence quality; it is not a probability."
          />
        </div>

        <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-muted-foreground">Vague question</dt>
            <dd>{assessment.ambiguity ? "Yes" : "No"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Model&apos;s own verdict</dt>
            <dd>{signals.model_status?.replace("_", " ") ?? "not asked"}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Policy</dt>
            <dd className="break-words">
              {assessment.policy} · {assessment.policy_version}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground" title="Signals, citation and claim checks; the model's own time is not included">
              Checks took
            </dt>
            <dd className="tabular-nums">{formatSeconds(assessment.latency_ms)}</dd>
          </div>
        </dl>

        {missing.length ? (
          <p className="text-xs text-muted-foreground">
            Not found in the passages: {missing.map((w) => `“${w}”`).join(", ")}
          </p>
        ) : null}

        {assessment.claims.length ? (
          <div className="space-y-2">
            <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Claim checks{assessment.nli_model ? ` (${assessment.nli_model})` : ""}
            </h3>
            <ul className="space-y-1.5" aria-label="Claim checks">
              {assessment.claims.map((claim, index) => (
                <li key={index} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-md border p-2 text-sm">
                  <span className="min-w-0 flex-1 break-words">{claim.claim}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {claim.passages.map((n) => `[${n}]`).join("")} · supported {percent(claim.entailment)} ·
                    contradicted {percent(claim.contradiction)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
