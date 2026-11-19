import type { Metadata } from "next";

import { RequireStaff } from "@/components/guards";
import { GapsPage } from "@/components/knowledge/gaps-page";

export const metadata: Metadata = { title: "Knowledge gaps" };

export default function Page() {
  return (
    <RequireStaff>
      <GapsPage />
    </RequireStaff>
  );
}
