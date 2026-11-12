import type { Metadata } from "next";

import { RequireAdmin } from "@/components/guards";
import { RoutingPage } from "@/components/settings/routing-page";

export const metadata: Metadata = { title: "Routing" };

export default function SettingsRoutingPage() {
  return (
    <RequireAdmin>
      <RoutingPage />
    </RequireAdmin>
  );
}
