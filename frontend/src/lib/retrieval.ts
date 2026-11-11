import type { AIRun } from "@/lib/api/client";

export type RequestedStrategy = "auto" | "hybrid" | "sparse" | "dense";

export const REQUESTED_STRATEGY_LABELS: Record<RequestedStrategy, string> = {
  auto: "Auto (adaptive)",
  hybrid: "Hybrid",
  sparse: "Keyword (BM25)",
  dense: "Semantic",
};

export const STRATEGY_LABELS: Record<string, string> = {
  sparse: "Keyword only",
  dense: "Semantic only",
  hybrid: "Hybrid",
  sparse_heavy: "Keyword-heavy hybrid",
  dense_heavy: "Semantic-heavy hybrid",
};

export const QUERY_TYPE_LABELS: Record<string, string> = {
  exact_identifier: "Exact identifier",
  terminology: "Terminology",
  natural_question: "Natural-language question",
  policy: "Policy question",
  ambiguous: "Ambiguous",
};

export const STAGE_LABELS: Record<string, string> = {
  analysis: "Analysis",
  sparse: "BM25",
  dense: "Semantic",
  fusion: "Fusion",
  rerank: "Rerank",
  generation: "Answer",
  total: "Total",
};

export function formatMs(ms: number): string {
  if (ms >= 10_000) return `${(ms / 1000).toFixed(1)} s`;
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

export function stageTimings(run: Pick<AIRun, "timings_ms">): [string, number][] {
  const order = ["analysis", "sparse", "dense", "fusion", "rerank", "generation", "total"];
  return order
    .filter((stage) => run.timings_ms[stage] !== undefined)
    .map((stage) => [stage, run.timings_ms[stage]]);
}
