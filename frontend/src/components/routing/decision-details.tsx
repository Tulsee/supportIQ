import { CheckIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { RoutingCandidate, RoutingOutcome, Schemas } from "@/lib/api/client";
import { OUTCOMES } from "@/lib/routing";
import { cn } from "@/lib/utils";

export function OutcomeBadge({ outcome, className }: { outcome: RoutingOutcome; className?: string }) {
  const { label, className: tone } = OUTCOMES[outcome];
  return <Badge className={cn("border-transparent", tone, className)}>{label}</Badge>;
}

/** The rules tried, in order, and the agents considered, ranked, with why others weren't picked. */
export function DecisionDetails({
  rules,
  candidates,
  showSkills = false,
}: {
  rules: Schemas["RuleCheckRead"][];
  candidates: RoutingCandidate[];
  showSkills?: boolean;
}) {
  return (
    <div className="space-y-4 text-sm">
      {rules.length ? (
        <div className="space-y-1.5">
          <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Rules tried</h4>
          <ol className="space-y-1">
            {rules.map((rule, index) => (
              <li key={`${rule.rule_id ?? rule.name}-${index}`} className="flex gap-2">
                {rule.matched ? (
                  <CheckIcon className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-label="Matched:" />
                ) : (
                  <XIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="Didn't match:" />
                )}
                <span>
                  <span className="font-medium">{rule.name}</span>
                  <span className="text-muted-foreground"> · {rule.detail}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {candidates.length ? (
        <div className="space-y-1.5">
          <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Agents</h4>
          <ol className="space-y-1.5">
            {candidates.map((candidate) => (
              <li key={candidate.user_id} className="flex items-start gap-2">
                <span
                  className={cn(
                    "flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums",
                    candidate.rank ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                  )}
                  aria-label={candidate.rank ? `Choice ${candidate.rank}` : "Not eligible"}
                >
                  {candidate.rank ?? "–"}
                </span>
                <span className="min-w-0">
                  <span className="font-medium">{candidate.name}</span>
                  <span className="text-muted-foreground">
                    {" · "}
                    {candidate.active_tickets} of {candidate.max_active_tickets} active
                    {showSkills ? ` · skill total ${candidate.skill_score}` : ""}
                  </span>
                  {candidate.reasons.length ? (
                    <span className="block text-xs text-amber-700 dark:text-amber-400">
                      {candidate.reasons.join("; ")}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}
