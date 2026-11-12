import type { Answer, AnswerCitation, AnswerStatus, ModelStatus } from "@/lib/api/client";

type Tone = "ok" | "warn" | "muted" | "error";

export const ANSWER_STATUS: Record<AnswerStatus, { label: string; description: string; tone: Tone }> = {
  answered: {
    label: "Answered",
    description: "Every citation points to one of the retrieved passages.",
    tone: "ok",
  },
  uncited: {
    label: "No citations",
    description:
      "The model answered without citing a passage, so the answer can't be traced to your documents.",
    tone: "warn",
  },
  declined: {
    label: "Not in documents",
    description: "The model found no answer in the retrieved passages.",
    tone: "muted",
  },
  no_passages: {
    label: "Nothing found",
    description: "No passages matched the question, so the model wasn't asked.",
    tone: "muted",
  },
  failed: {
    label: "Failed",
    description: "The model produced no answer.",
    tone: "error",
  },
  clarify: {
    label: "Needs clarification",
    description: "The question could mean several things, so the assistant asked which one.",
    tone: "muted",
  },
  escalated: {
    label: "Escalated",
    description: "The documents disagree about this, so a person should confirm the answer.",
    tone: "warn",
  },
  abstained: {
    label: "Not answered",
    description: "The evidence didn't support a reliable answer, so the assistant held back.",
    tone: "muted",
  },
};

/** Why the evidence policy decided what it did (assessment.reason). */
export const DECISION_REASONS: Record<string, string> = {
  no_passages: "No passages matched the question.",
  low_relevance: "The best passage was only weakly related to the question.",
  vague_question: "The question has no specific subject, so it was clarified before asking the model.",
  low_coverage: "Too few of the question's words appear in the passages.",
  model_ambiguous: "The model found that the question could mean several things.",
  conflicting_sources: "The model found that the passages give different answers.",
  model_not_found: "The model found no answer in the passages.",
  incomplete_evidence: "The passages answer only part of the question.",
  no_citations: "The answer didn't cite any passage.",
  contradicted_by_source: "A claim in the answer is contradicted by the passage it cites.",
  weak_grounding: "The cited passages don't clearly state the answer's claims.",
  model_error: "The model produced no answer.",
  supported: "The evidence is relevant and every claim cites a passage.",
};

export const DECISION_LABELS: Record<string, string> = {
  answer: "Answer",
  clarify: "Clarify",
  escalate: "Escalate",
  abstain: "Abstain",
};

export const PROVIDER_LABELS: Record<string, string> = {
  ollama: "Ollama",
  anthropic: "Anthropic",
};

export type AnswerPart =
  | { kind: "text"; text: string }
  | { kind: "cite"; passage: number; offset: number };

/** The answer text as plain runs and citation markers (`[n]`). */
export function answerParts(text: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ kind: "text", text: text.slice(last, index) });
    parts.push({ kind: "cite", passage: Number(match[1]), offset: index });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

/** Each cited passage once, by passage number. */
export function citedSources(answer: Answer): AnswerCitation[] {
  const byPassage = new Map<number, AnswerCitation>();
  for (const citation of answer.citations) {
    if (!byPassage.has(citation.passage_number)) byPassage.set(citation.passage_number, citation);
  }
  return [...byPassage.values()].sort((a, b) => a.passage_number - b.passage_number);
}

/** "qwen3.5:4b" + where it runs, for a one-line description of the model. */
export function modelLabel(model: Pick<ModelStatus, "provider" | "model">): string {
  const provider = PROVIDER_LABELS[model.provider] ?? model.provider;
  return model.provider === "ollama"
    ? `${model.model} (${provider}, on this server)`
    : `${model.model} (${provider})`;
}

export function formatSeconds(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}
