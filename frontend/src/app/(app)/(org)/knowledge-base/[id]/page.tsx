import type { Metadata } from "next";
import { Suspense } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { RequireStaff } from "@/components/guards";
import { DocumentDetail } from "@/components/knowledge/document-detail";

export const metadata: Metadata = { title: "Document" };

export default function Page() {
  // The document id is only known at request time; useParams needs a Suspense boundary.
  return (
    <RequireStaff>
      <Suspense fallback={<FullPageSpinner className="min-h-[50svh]" />}>
        <DocumentDetail />
      </Suspense>
    </RequireStaff>
  );
}
