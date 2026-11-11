import type { Metadata } from "next";

import { MembersPage } from "@/components/settings/members-page";

export const metadata: Metadata = { title: "Members" };

export default function SettingsMembersPage() {
  return <MembersPage />;
}
