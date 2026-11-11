import type { Metadata } from "next";

import { PasswordCard } from "@/components/account/password-card";
import { ProfileCard } from "@/components/account/profile-card";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Account settings" };

export default function AccountPage() {
  return (
    <>
      <PageHeader title="Account settings" description="Your profile and sign-in details." />
      <div className="grid max-w-2xl gap-6">
        <ProfileCard />
        <PasswordCard />
      </div>
    </>
  );
}
