import type { Metadata } from "next";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { RequireStaff } from "@/components/guards";
import { RunDetail } from "@/components/retrieval/run-detail";

export const metadata: Metadata = { title: "AI run" };

export default function Page() {
  // The run id is only known at request time; useParams needs a Suspense boundary.
  return (
    <RequireStaff>
      <Suspense fallback={<FullPageSpinner className="min-h-[50svh]" />}>
        <RunDetail />
      </Suspense>
    </RequireStaff>
  );
}
