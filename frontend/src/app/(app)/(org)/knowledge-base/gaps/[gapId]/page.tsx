import type { Metadata } from "next";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { RequireStaff } from "@/components/guards";
import { GapDetail } from "@/components/knowledge/gap-detail";

export const metadata: Metadata = { title: "Knowledge gap" };

export default function Page() {
  // The gap id is only known at request time; useParams needs a Suspense boundary.
  return (
    <RequireStaff>
      <Suspense fallback={<FullPageSpinner className="min-h-[50svh]" />}>
        <GapDetail />
      </Suspense>
    </RequireStaff>
  );
}
