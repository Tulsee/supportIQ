import type { Metadata } from "next";

import { RequireStaff } from "@/components/guards";
import { RunsList } from "@/components/retrieval/runs-list";

export const metadata: Metadata = { title: "AI inspector" };

export default function Page() {
  return (
    <RequireStaff>
      <RunsList />
    </RequireStaff>
  );
}
