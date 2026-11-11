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
