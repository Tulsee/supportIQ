import type { Metadata } from "next";

import { OrganizationSettings } from "@/components/settings/organization-settings";

export const metadata: Metadata = { title: "Organization settings" };

export default function SettingsPage() {
  return <OrganizationSettings />;
}
