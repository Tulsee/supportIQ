"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, LockIcon, SendIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { refreshTickets } from "@/components/tickets/hooks";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, tenant, unwrap, type Ticket } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { cn } from "@/lib/utils";

type Mode = "reply" | "note";

/** Text to put in the reply box (e.g. an AI suggestion); a new `key` applies it again. */
export type ReplyDraft = { text: string; key: number };

export function ReplyBox({
  ticket,
  audience,
  draft,
}: {
  ticket: Ticket;
  audience: "staff" | "customer";
  draft?: ReplyDraft | null;
}) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>("reply");
  const [body, setBody] = useState("");
  const [appliedDraft, setAppliedDraft] = useState<number | null>(null);
  if (draft && draft.key !== appliedDraft) {
    // A new draft replaces the text (adjusting state during render, not in an effect).
    setAppliedDraft(draft.key);
    setMode("reply");
    setBody(draft.text);
  }
  const isNote = audience === "staff" && mode === "note";

  const send = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/messages", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
          body: { body, is_internal: isNote },
        }),
      ),
    onSuccess: async () => {
      setBody("");
      // The reply can also change the ticket (a customer reply reopens it).
      await refreshTickets(queryClient, organization.id);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const canSend = body.trim().length > 0 && !send.isPending;
  const submit = () => {
    if (canSend) send.mutate();
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className={cn(
        "space-y-3 rounded-xl border bg-card p-3 shadow-xs",
        isNote && "border-dashed border-amber-400/70 bg-amber-50/60 dark:bg-amber-950/20",
      )}
    >
      {audience === "staff" ? (
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList>
            <TabsTrigger value="reply">Reply to customer</TabsTrigger>
            <TabsTrigger value="note">
              <LockIcon aria-hidden />
              Internal note
            </TabsTrigger>
          </TabsList>
        </Tabs>
      ) : null}
      <Textarea
        aria-label={isNote ? "Internal note" : "Reply"}
        placeholder={
          isNote
            ? "Add a note for your team. The customer won't see it."
            : audience === "staff"
              ? `Reply to ${ticket.customer.name}…`
              : "Write a reply…"
        }
        rows={4}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            submit();
          }
        }}
        className="resize-y border-0 bg-transparent px-1 shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center justify-between gap-2">
        <span className="hidden text-xs text-muted-foreground sm:inline">Ctrl+Enter to send</span>
        <Button type="submit" disabled={!canSend} className="ml-auto">
          {send.isPending ? (
            <Loader2Icon className="animate-spin" aria-hidden />
          ) : (
            <SendIcon aria-hidden />
          )}
          {isNote ? "Add note" : "Send reply"}
        </Button>
      </div>
    </form>
  );
}
