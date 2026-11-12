import type { Metadata } from "next";

import { AnalyticsPage } from "@/components/analytics/analytics-page";
import { RequireStaff } from "@/components/guards";

export const metadata: Metadata = { title: "Analytics" };

export default function Page() {
  return (
    <RequireStaff>
      <AnalyticsPage />
    </RequireStaff>
  );
}
