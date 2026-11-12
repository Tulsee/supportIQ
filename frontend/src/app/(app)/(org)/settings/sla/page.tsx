import type { Metadata } from "next";

import { RequireAdmin } from "@/components/guards";
import { SlaPage } from "@/components/settings/sla-page";

export const metadata: Metadata = { title: "Service levels" };

export default function SettingsSlaPage() {
  return (
    <RequireAdmin>
      <SlaPage />
    </RequireAdmin>
  );
}
