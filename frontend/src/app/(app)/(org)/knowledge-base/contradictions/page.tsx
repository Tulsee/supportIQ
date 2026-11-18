import type { Metadata } from "next";

import { RequireStaff } from "@/components/guards";
import { ContradictionsPage } from "@/components/knowledge/contradictions-page";

export const metadata: Metadata = { title: "Contradictions" };

export default function Page() {
  return (
    <RequireStaff>
      <ContradictionsPage />
    </RequireStaff>
  );
}
