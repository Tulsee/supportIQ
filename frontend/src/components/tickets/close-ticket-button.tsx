"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LockIcon } from "lucide-react";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { refreshTickets } from "@/components/tickets/hooks";
import { api, errorMessage, tenant, unwrap, type Ticket } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

const COPY = {
  customer: {
    button: "Close request",
    title: "Close this request?",
    description:
      "Close it if you don't need help with it anymore. You won't be able to reply afterwards; if you need help again, open a new request.",
    done: "Request closed",
  },
  staff: {
    button: "Close ticket",
    title: "Close this ticket?",
    description:
      "Closed tickets can't take new replies and can't be reopened. If the customer might still reply, mark it resolved instead.",
    done: "Ticket closed",
  },
} as const;

/** Close a ticket for good, after confirming: staff any ticket, customers their own. */
export function CloseTicketButton({
  ticket,
  audience,
}: {
  ticket: Ticket;
  audience: "customer" | "staff";
}) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const copy = COPY[audience];

  async function close() {
    try {
      const updated = await unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/close", {
          params: { path: { ticket_id: ticket.id }, header: tenant(organization.id) },
        }),
      );
      queryClient.setQueryData(queryKeys.ticket(organization.id, ticket.id), updated);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.ticketEvents(organization.id, ticket.id),
        refetchType: "all",
      });
      await refreshTickets(queryClient, organization.id);
      toast.success(copy.done);
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  }

  return (
    <ConfirmAction
      triggerProps={{ variant: "outline", size: "sm" }}
      title={copy.title}
      description={copy.description}
      confirmLabel={copy.button}
      onConfirm={close}
    >
      <LockIcon aria-hidden />
      {copy.button}
    </ConfirmAction>
  );
}
