import type { Metadata } from "next";

import { NotificationsCard } from "@/components/account/notifications-card";
import { PasswordCard } from "@/components/account/password-card";
import { ProfileCard } from "@/components/account/profile-card";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Account settings" };

export default function AccountPage() {
  return (
    <>
      <PageHeader title="Account settings" description="Your profile, sign-in details and notifications." />
      <div className="grid max-w-2xl gap-6">
        <ProfileCard />
        <PasswordCard />
        <NotificationsCard />
      </div>
    </>
  );
}
