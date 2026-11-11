import type { Metadata } from "next";

import { AdminOrganizations } from "@/components/admin/admin-organizations";

export const metadata: Metadata = { title: "All organizations" };

export default function AdminOrganizationsPage() {
  return <AdminOrganizations />;
}
