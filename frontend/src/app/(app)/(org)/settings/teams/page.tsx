import type { Metadata } from "next";

import { RequireAdmin } from "@/components/guards";
import { TeamsPage } from "@/components/settings/teams-page";

export const metadata: Metadata = { title: "Teams and agents" };

export default function SettingsTeamsPage() {
  return (
    <RequireAdmin>
      <TeamsPage />
    </RequireAdmin>
  );
}
