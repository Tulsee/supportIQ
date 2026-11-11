import { CircleAlertIcon, TriangleAlertIcon } from "lucide-react";

import { AnswerText } from "@/components/answers/answer-text";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { Answer } from "@/lib/api/client";
import { ANSWER_STATUS, PROVIDER_LABELS, formatSeconds } from "@/lib/answers";
import { cn } from "@/lib/utils";

const TONE_CLASSES = {
  ok: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  warn: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  muted: "",
  error: "",
} as const;

export function AnswerStatusBadge({ status }: { status: Answer["status"] }) {
  const info = ANSWER_STATUS[status];
  return (
    <Badge
      variant={info.tone === "error" ? "destructive" : info.tone === "muted" ? "secondary" : "default"}
      className={cn("whitespace-nowrap", TONE_CLASSES[info.tone])}
      title={info.description}
    >
      {info.label}
    </Badge>
  );
}

function Setting({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words tabular-nums">{children}</dd>
    </div>
  );
}

const TIMING_LABELS: Record<string, string> = {
  load: "Model load",
  prompt: "Reading the prompt",
  output: "Writing",
  total: "Total",
};

/** The generated answer of a run: what came back, and what the model was given. */
export function AnswerCard({ answer, passageId }: { answer: Answer; passageId: (n: number) => string }) {
  const info = ANSWER_STATUS[answer.status];
  const config = answer.config as Record<string, unknown>;
  const timings = ["load", "prompt", "output", "total"].filter((k) => answer.timings_ms[k] !== undefined);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <h2>Answer</h2>
          <AnswerStatusBadge status={answer.status} />
        </CardTitle>
        <CardDescription>{info.description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {answer.status === "failed" ? (
          <Alert variant="destructive">
            <CircleAlertIcon aria-hidden />
            <AlertTitle>No answer</AlertTitle>
            <AlertDescription>{answer.error ?? info.description}</AlertDescription>
          </Alert>
        ) : (
          <AnswerText
            answer={answer}
            sourceId={passageId}
            className="rounded-lg border bg-muted/30 p-3"
          />
        )}

        {answer.invalid_citations.length ? (
          <Alert>
            <TriangleAlertIcon aria-hidden />
            <AlertTitle>
              Removed {answer.invalid_citations.length === 1 ? "a citation" : "citations"} to
              passages the model wasn&apos;t given
            </AlertTitle>
            <AlertDescription>
              It cited {answer.invalid_citations.map((n) => `[${n}]`).join(", ")}, but only{" "}
              {answer.passage_count} passage{answer.passage_count === 1 ? " was" : "s were"} in the
              prompt. The raw output below shows what it wrote.
            </AlertDescription>
          </Alert>
        ) : null}
        {answer.stop_reason === "length" ? (
          <Alert>
            <TriangleAlertIcon aria-hidden />
            <AlertTitle>The answer hit the output limit</AlertTitle>
            <AlertDescription>It may be cut off.</AlertDescription>
          </Alert>
        ) : null}
        {answer.omitted_passages > 0 ? (
          <p className="text-xs text-muted-foreground">
            {answer.omitted_passages} selected passage{answer.omitted_passages === 1 ? "" : "s"}{" "}
            didn&apos;t fit the model&apos;s context window and {answer.omitted_passages === 1 ? "was" : "were"} left out of the prompt.
          </p>
        ) : null}

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Setting label="Model">{answer.model}</Setting>
          <Setting label="Provider">{PROVIDER_LABELS[answer.provider] ?? answer.provider}</Setting>
          {answer.model_version ? (
            <Setting label="Version">
              <span title={answer.model_version} className="font-mono text-xs">
                {answer.model_version.slice(0, 12)}
              </span>
            </Setting>
          ) : null}
          {"temperature" in config ? <Setting label="Temperature">{String(config.temperature)}</Setting> : null}
          {"effort" in config ? <Setting label="Effort">{String(config.effort)}</Setting> : null}
          {"max_output_tokens" in config ? (
            <Setting label="Output limit">{String(config.max_output_tokens)} tokens</Setting>
          ) : null}
          {"context_tokens" in config ? (
            <Setting label="Context window">{String(config.context_tokens)} tokens</Setting>
          ) : null}
          <Setting label="Prompt">{answer.prompt_version}</Setting>
          <Setting label="Passages in prompt">{answer.passage_count}</Setting>
          {answer.input_tokens !== null ? (
            <Setting label="Tokens in → out">
              {answer.input_tokens} → {answer.output_tokens ?? "—"}
            </Setting>
          ) : null}
          {answer.stop_reason ? <Setting label="Stopped">{answer.stop_reason}</Setting> : null}
        </dl>

        {timings.length ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
            {timings.map((key) => (
              <span key={key} className={key === "total" ? "font-medium text-foreground" : ""}>
                {TIMING_LABELS[key]} {formatSeconds(answer.timings_ms[key])}
              </span>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PromptBlock({ title, text, open = false }: { title: string; text: string; open?: boolean }) {
  return (
    <details className="group rounded-lg border" open={open}>
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium select-none">
        {title}
        <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">
          {text.length.toLocaleString()} characters
        </span>
      </summary>
      <pre className="max-h-[32rem] overflow-auto border-t bg-muted/30 p-3 text-xs leading-relaxed whitespace-pre-wrap">
        {text}
      </pre>
    </details>
  );
}

/** Exactly what the model was given and what it returned. */
export function PromptCard({ answer }: { answer: Answer }) {
  const showRaw = answer.raw_output !== null && answer.raw_output !== answer.text;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Prompt</h2>
        </CardTitle>
        <CardDescription>
          Exactly what the model was given{showRaw ? ", and what it returned before the citation check" : ""}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <PromptBlock title="Instructions (system)" text={answer.system_prompt} />
        <PromptBlock title="Passages and question (user)" text={answer.user_prompt} />
        {showRaw ? <PromptBlock title="Raw model output" text={answer.raw_output ?? ""} /> : null}
      </CardContent>
    </Card>
  );
}
