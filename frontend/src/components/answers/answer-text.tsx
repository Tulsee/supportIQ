import Link from "next/link";

import type { Answer, RetrievedPassage } from "@/lib/api/client";
import { answerParts, citedSources } from "@/lib/answers";
import { pageLabel } from "@/lib/documents";
import { cn } from "@/lib/utils";

function sourceDescription(answer: Answer, passage: number): string {
  const citation = answer.citations.find((c) => c.passage_number === passage);
  if (!citation) return `Source ${passage}`;
  const where = [
    citation.document?.title ?? "Deleted document",
    pageLabel(citation),
    citation.section_heading ? `§ ${citation.section_heading}` : null,
  ];
  return `Source ${passage}: ${where.filter(Boolean).join(", ")}`;
}

function CitationChip({ passage, href, label }: { passage: number; href: string; label: string }) {
  return (
    <a
      href={href}
      aria-label={label}
      title={label}
      className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded bg-primary/10 px-1 align-text-top text-[11px] font-semibold text-primary tabular-nums no-underline hover:bg-primary/20 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    >
      {passage}
    </a>
  );
}

/** The answer with its `[n]` markers as links to the sources they cite. */
export function AnswerText({
  answer,
  sourceId,
  className,
}: {
  answer: Answer;
  /** The element id of source n (the sources list, or the passage cards). */
  sourceId: (passage: number) => string;
  className?: string;
}) {
  if (!answer.text) return null;
  return (
    <p className={cn("text-sm leading-relaxed whitespace-pre-wrap", className)}>
      {answerParts(answer.text).map((part, index) =>
        part.kind === "text" ? (
          <span key={index}>{part.text}</span>
        ) : (
          <CitationChip
            key={index}
            passage={part.passage}
            href={`#${sourceId(part.passage)}`}
            label={sourceDescription(answer, part.passage)}
          />
        ),
      )}
    </p>
  );
}

/** The cited passages: document, page, section and version, with an excerpt. */
export function AnswerSources({
  answer,
  passages,
  sourceId,
}: {
  answer: Answer;
  /** The run's selected passages (passage n is passages[n - 1]), for excerpts. */
  passages?: RetrievedPassage[];
  sourceId: (passage: number) => string;
}) {
  const sources = citedSources(answer);
  if (sources.length === 0) return null;
  return (
    <ol className="space-y-2" aria-label="Sources">
      {sources.map((citation) => {
        const excerpt = passages?.[citation.passage_number - 1]?.chunk?.content;
        const page = pageLabel(citation);
        return (
          <li
            key={citation.passage_number}
            id={sourceId(citation.passage_number)}
            className="flex scroll-mt-24 gap-2.5 rounded-md p-1.5 text-sm target:bg-primary/5 target:ring-1 target:ring-primary/30"
          >
            <span className="mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded bg-primary/10 px-1 text-xs font-semibold text-primary tabular-nums">
              {citation.passage_number}
            </span>
            <div className="min-w-0 space-y-0.5">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                {citation.document ? (
                  <Link
                    href={`/knowledge-base/${citation.document.id}`}
                    className="font-medium break-words hover:underline"
                  >
                    {citation.document.title}
                  </Link>
                ) : (
                  <span className="text-muted-foreground italic">Deleted document</span>
                )}
                <span className="text-xs text-muted-foreground">
                  {[page, citation.section_heading ? `§ ${citation.section_heading}` : null, `v${citation.document_version}`]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
              {excerpt ? (
                <p className="line-clamp-2 text-xs text-muted-foreground">{excerpt}</p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
