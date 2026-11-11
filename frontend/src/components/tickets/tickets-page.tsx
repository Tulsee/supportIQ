"use client";

import { CustomerTickets } from "@/components/tickets/customer-tickets";
import { TicketsInbox } from "@/components/tickets/tickets-inbox";
import { useActiveOrganization } from "@/lib/org/org-provider";

/** Staff work the inbox; customers see their own requests. */
export function TicketsPage() {
  const organization = useActiveOrganization();
  return organization.role === "customer" ? <CustomerTickets /> : <TicketsInbox />;
}
