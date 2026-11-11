"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FileUpIcon, Loader2Icon, UploadIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import type { ReactElement } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage, tenant, unwrap, type KnowledgeDocument } from "@/lib/api/client";
import { ACCEPTED_EXTENSIONS, checkFile, formatBytes, uploadFormData } from "@/lib/documents";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

/**
 * Upload a new document, or (with ``document``) a new version of one. Indexing
 * then runs in the background; the list shows its progress.
 */
export function UploadDocumentDialog({
  document,
  trigger,
}: {
  document?: KnowledgeDocument;
  trigger?: ReactElement;
}) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const uid = useId();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [pending, setPending] = useState(false);
  const isVersion = document !== undefined;

  const reset = () => {
    setFile(null);
    setTitle("");
    setError(null);
    setDragging(false);
  };

  const choose = (candidate: File | undefined) => {
    if (!candidate) return;
    setError(checkFile(candidate));
    setFile(candidate);
  };

  const submit = async () => {
    if (!file || checkFile(file)) return;
    setPending(true);
    setError(null);
    try {
      const header = tenant(organization.id);
      const uploaded = isVersion
        ? await unwrap(
            api.POST("/api/v1/documents/{document_id}/versions", {
              params: { path: { document_id: document.id }, header },
              body: { file: file as unknown as string },
              bodySerializer: uploadFormData,
            }),
          )
        : await unwrap(
            api.POST("/api/v1/documents", {
              params: { header },
              body: { file: file as unknown as string, title: title.trim() || null },
              bodySerializer: uploadFormData,
            }),
          );
      await queryClient.invalidateQueries({
        queryKey: queryKeys.documents(organization.id),
        refetchType: "all",
      });
      if (uploaded.status === "failed") toast.error(uploaded.error ?? "Indexing couldn't start");
      else toast.success(isVersion ? `Version ${uploaded.version} uploaded` : `${uploaded.title} uploaded`);
      setOpen(false);
      reset();
      if (!isVersion) router.push(`/knowledge-base/${uploaded.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger
        render={
          trigger ?? (
            <Button>
              <UploadIcon aria-hidden />
              Upload document
            </Button>
          )
        }
      />
      <DialogContent className="sm:max-w-lg">
        <form
          noValidate
          className="grid gap-6"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <DialogHeader>
            <DialogTitle>{isVersion ? "Upload a new version" : "Upload a document"}</DialogTitle>
            <DialogDescription>
              {isVersion
                ? `Replaces the file of “${document.title}”. The current version stays searchable until the new one is indexed.`
                : "PDF, Word (.docx), text, or Markdown, up to 20 MB. It's indexed in the background."}
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <label
              htmlFor={`${uid}-file`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                choose(event.dataTransfer.files[0]);
              }}
              className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm transition-colors",
                dragging ? "border-primary bg-primary/5" : "hover:bg-muted/50",
              )}
            >
              <FileUpIcon className="size-8 text-muted-foreground" aria-hidden />
              {file ? (
                <span>
                  <span className="font-medium">{file.name}</span>
                  <span className="text-muted-foreground"> · {formatBytes(file.size)}</span>
                </span>
              ) : (
                <span>
                  <span className="font-medium text-primary">Choose a file</span> or drag it here
                </span>
              )}
              <input
                ref={inputRef}
                id={`${uid}-file`}
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="sr-only"
                onChange={(event) => choose(event.target.files?.[0])}
              />
            </label>
            {isVersion ? null : (
              <Field>
                <FieldLabel htmlFor={`${uid}-title`}>Title</FieldLabel>
                <Input
                  id={`${uid}-title`}
                  value={title}
                  maxLength={300}
                  placeholder={file ? file.name.replace(/\.[^.]+$/, "") : "Defaults to the file name"}
                  onChange={(event) => setTitle(event.target.value)}
                />
                <FieldDescription>Optional. Shown in search results and citations.</FieldDescription>
              </Field>
            )}
            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={!file || !!checkFile(file) || pending}>
              {pending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Upload
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
