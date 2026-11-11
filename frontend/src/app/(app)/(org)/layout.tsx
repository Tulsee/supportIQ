import type { ReactNode } from "react";

import { RequireOrganization } from "@/components/guards";
import { TicketLiveUpdates } from "@/lib/realtime/ticket-stream";

/** Pages that act within an organization (tenant-scoped data). */
export default function OrganizationLayout({ children }: { children: ReactNode }) {
  return (
    <RequireOrganization>
      <TicketLiveUpdates />
      {children}
    </RequireOrganization>
  );
}
