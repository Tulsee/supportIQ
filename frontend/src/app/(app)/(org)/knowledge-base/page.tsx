import type { Metadata } from "next";

import { RequireStaff } from "@/components/guards";
import { DocumentsPage } from "@/components/knowledge/documents-page";

export const metadata: Metadata = { title: "Knowledge base" };

export default function Page() {
  return (
    <RequireStaff>
      <DocumentsPage />
    </RequireStaff>
  );
}
