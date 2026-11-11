import type { Metadata } from "next";
import { Suspense } from "react";

import { Brand } from "@/components/brand";
import { AcceptInvitation } from "@/components/invitations/accept-invitation";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Accept invitation" };

export default function AcceptInvitationPage() {
  return (
    <main className="flex min-h-svh flex-1 flex-col items-center justify-center gap-6 bg-muted/40 p-6">
      <Brand />
      <div className="w-full max-w-sm">
        {/* AcceptInvitation renders only in the browser (use(browser())). */}
        <Suspense fallback={<Skeleton className="h-72 w-full rounded-xl" />}>
          <AcceptInvitation />
        </Suspense>
      </div>
    </main>
  );
}
