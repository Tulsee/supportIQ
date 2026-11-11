import type { Metadata } from "next";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { TicketDetail } from "@/components/tickets/ticket-detail";

export const metadata: Metadata = { title: "Ticket" };

export default function Page() {
  // The ticket id is only known at request time; useParams needs a Suspense boundary.
  return (
    <Suspense fallback={<FullPageSpinner className="min-h-[50svh]" />}>
      <TicketDetail />
    </Suspense>
  );
}
