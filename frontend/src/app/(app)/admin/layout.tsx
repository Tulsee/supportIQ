import type { ReactNode } from "react";

import { RequireSuperAdmin } from "@/components/guards";

/** Platform administration. No active organization needed. */
export default function AdminLayout({ children }: { children: ReactNode }) {
  return <RequireSuperAdmin>{children}</RequireSuperAdmin>;
}
