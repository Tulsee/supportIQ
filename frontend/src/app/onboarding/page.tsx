import type { Metadata } from "next";

import { Brand } from "@/components/brand";
import { AuthGuard } from "@/components/guards";
import { CreateOrganizationForm } from "@/components/organizations/create-organization-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Create your organization" };

export default function OnboardingPage() {
  return (
    <main className="flex min-h-svh flex-1 flex-col items-center justify-center gap-6 bg-muted/40 p-6">
      <Brand />
      <div className="w-full max-w-md">
        <AuthGuard>
          <Card>
            <CardHeader>
              <CardTitle className="text-xl"><h1>Create your organization</h1></CardTitle>
              <CardDescription>
                An organization holds your team, customers, tickets, and knowledge base. You&apos;ll
                be its admin.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <CreateOrganizationForm />
              <p className="text-sm text-muted-foreground">
                Joining an existing team? Ask an admin to invite you. The invitation arrives by
                email.
              </p>
            </CardContent>
          </Card>
        </AuthGuard>
      </div>
    </main>
  );
}
