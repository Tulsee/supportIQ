import type { Metadata } from "next";

import { RequireAdmin } from "@/components/guards";
import { AuditLogPage } from "@/components/settings/audit-log-page";

export const metadata: Metadata = { title: "Audit log" };

export default function SettingsAuditLogPage() {
  return (
    <RequireAdmin>
      <AuditLogPage />
    </RequireAdmin>
  );
}
