import type { KnowledgeDocument } from "@/lib/api/client";

type Status = KnowledgeDocument["status"];
type FileType = KnowledgeDocument["file_type"];

export const DOCUMENT_STATUS_LABELS: Record<Status, string> = {
  queued: "Queued",
  processing: "Processing",
  indexed: "Indexed",
  failed: "Failed",
};

export const FILE_TYPE_LABELS: Record<FileType, string> = {
  pdf: "PDF",
  docx: "Word",
  txt: "Text",
  markdown: "Markdown",
};

/** Mirrors the API's checks for instant feedback; the API validates for real. */
export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown"];
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

export function isIndexing(document: Pick<KnowledgeDocument, "status">): boolean {
  return document.status === "queued" || document.status === "processing";
}

export function checkFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return "Upload a PDF, Word (.docx), text, or Markdown file.";
  }
  if (file.size > MAX_UPLOAD_BYTES) return "Files can be at most 20 MB.";
  if (file.size === 0) return "The file is empty.";
  return null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function pageLabel(chunk: { page_number: number | null; page_end: number | null }): string | null {
  if (chunk.page_number === null) return null;
  return chunk.page_end && chunk.page_end !== chunk.page_number
    ? `Pages ${chunk.page_number}–${chunk.page_end}`
    : `Page ${chunk.page_number}`;
}

/** openapi-fetch serializer for multipart uploads. */
export function uploadFormData(body: { file: unknown; title?: string | null }): FormData {
  const form = new FormData();
  form.append("file", body.file as Blob);
  if (body.title) form.append("title", body.title);
  return form;
}
