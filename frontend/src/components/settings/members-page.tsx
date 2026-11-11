"use client";

import { PageHeader } from "@/components/page-header";
import { InvitationsPanel } from "@/components/settings/invitations-panel";
import { MembersPanel } from "@/components/settings/members-panel";
import { useActiveOrganization } from "@/lib/org/org-provider";

export function MembersPage() {
  const organization = useActiveOrganization();
  return (
    <>
      <PageHeader
        title="Members"
        description="Who can access this organization, and what they can do."
      />
      <div className="space-y-6">
        <MembersPanel />
        {organization.role === "admin" ? <InvitationsPanel /> : null}
      </div>
    </>
  );
}
