import type { ReactNode } from "react";

import { RequireOrganization } from "@/components/guards";

/** Pages that act within an organization (tenant-scoped data). */
export default function OrganizationLayout({ children }: { children: ReactNode }) {
  return <RequireOrganization>{children}</RequireOrganization>;
}
