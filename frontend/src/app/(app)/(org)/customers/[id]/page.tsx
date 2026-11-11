import type { Metadata } from "next";
import { Suspense } from "react";

import { CustomerDetail } from "@/components/customers/customer-detail";
import { FullPageSpinner } from "@/components/full-page-spinner";
import { RequireStaff } from "@/components/guards";

export const metadata: Metadata = { title: "Customer" };

export default function Page() {
  // The customer id is only known at request time; useParams needs a Suspense boundary.
  return (
    <RequireStaff>
      <Suspense fallback={<FullPageSpinner className="min-h-[50svh]" />}>
        <CustomerDetail />
      </Suspense>
    </RequireStaff>
  );
}
