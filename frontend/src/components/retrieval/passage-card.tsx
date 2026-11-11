import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { RetrievedPassage } from "@/lib/api/client";
import { pageLabel } from "@/lib/documents";
import { cn } from "@/lib/utils";

function Score({ label, title, children }: { label: string; title: string; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] tabular-nums"
      title={title}
    >
      <span className="text-muted-foreground">{label}</span>
      {children}
    </span>
  );
}

/** One retrieved passage with its score at every stage. */
export function PassageCard({
  passage,
  dimmed = false,
  id,
}: {
  passage: RetrievedPassage;
  dimmed?: boolean;
  /** Anchor for citations that point here. */
  id?: string;
}) {
  const page = passage.chunk ? pageLabel(passage.chunk) : null;
  return (
    <li
      id={id}
      className={cn(
        "scroll-mt-24 rounded-lg border p-3 target:ring-2 target:ring-primary/40",
        dimmed && "bg-muted/30",
      )}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        {passage.final_rank ? (
          <span className="font-semibold text-foreground">#{passage.final_rank}</span>
        ) : null}
        {passage.document ? (
          <Link
            href={`/knowledge-base/${passage.document.id}`}
            className="font-medium text-foreground hover:underline"
          >
            {passage.document.title}
          </Link>
        ) : (
          <span className="italic">Deleted document</span>
        )}
        {page ? <span>{page}</span> : null}
        {passage.chunk?.section_heading ? <span>§ {passage.chunk.section_heading}</span> : null}
        {passage.relevance !== null ? (
          <span className="ml-auto flex items-center gap-1.5" title="Cross-encoder relevance">
            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted" aria-hidden>
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: `${Math.round(passage.relevance * 100)}%` }}
              />
            </span>
            <span className="font-medium text-foreground tabular-nums">
              {Math.round(passage.relevance * 100)}%
            </span>
          </span>
        ) : null}
      </div>
      {passage.chunk ? (
        <p className="line-clamp-4 text-sm whitespace-pre-wrap">{passage.chunk.content}</p>
      ) : (
        <p className="text-sm text-muted-foreground italic">
          This passage was replaced when the document was re-indexed (chunk {passage.chunk_index + 1}
          , version {passage.document_version}).
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Score label="BM25" title="Keyword (BM25) rank and score">
          {passage.sparse ? `#${passage.sparse.rank} · ${passage.sparse.score.toFixed(2)}` : "—"}
        </Score>
        <Score label="Semantic" title="Vector-search rank and cosine similarity">
          {passage.dense ? `#${passage.dense.rank} · ${passage.dense.score.toFixed(3)}` : "—"}
        </Score>
        <Score label="Fused" title="Reciprocal Rank Fusion rank and score">
          #{passage.fused_rank} · {passage.fused_score.toFixed(4)}
        </Score>
        {passage.rerank_score !== null ? (
          <Score label="Rerank" title="Cross-encoder score (logit)">
            {passage.rerank_score.toFixed(2)}
          </Score>
        ) : null}
        {passage.document_version && passage.chunk ? (
          <Badge variant="outline" className="text-[11px]">
            v{passage.document_version}
          </Badge>
        ) : null}
      </div>
    </li>
  );
}
