import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell/app-shell";

/**
 * Signed-in application shell. Organization-scoped pages live in the (org) group,
 * which also requires an active organization; account and platform-admin pages
 * work without one.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
