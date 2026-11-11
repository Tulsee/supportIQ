"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleCheckIcon, CircleIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, unwrap } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/lib/roles";

type Step = { title: string; done: boolean; href?: string; note?: string };

export function DashboardOverview() {
  const user = useCurrentUser();
  const organization = useActiveOrganization();
  const isAdmin = organization.role === "admin";

  const members = useQuery({
    queryKey: queryKeys.members(organization.id, { limit: 1 }),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/organizations/{org_id}/members", {
          params: { path: { org_id: organization.id }, query: { limit: 1 } },
        }),
      ),
    enabled: organization.role !== "customer", // customers can't list members
  });

  const steps: Step[] = [
    { title: "Create your organization", done: true },
    {
      title: "Invite your team",
      done: (members.data?.total ?? 0) > 1,
      href: isAdmin ? "/settings/members" : undefined,
    },
    { title: "Upload knowledge base documents", done: false, note: "Coming soon" },
    { title: "Answer customer questions with cited AI", done: false, note: "Coming soon" },
  ];

  return (
    <>
      <PageHeader
        title={`Welcome, ${user.full_name.split(" ")[0]}`}
        description={
          <>
            {organization.name} · <Badge variant="secondary">{ROLE_LABELS[organization.role]}</Badge>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle><h2>Getting started</h2></CardTitle>
            <CardDescription>Set up {organization.name} for AI-assisted support.</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="space-y-3">
              {steps.map((step) => (
                <li key={step.title} className="flex items-center gap-3 text-sm">
                  {step.done ? (
                    <CircleCheckIcon className="size-5 text-primary" aria-label="Done" />
                  ) : (
                    <CircleIcon className="size-5 text-muted-foreground" aria-label="To do" />
                  )}
                  {step.href && !step.done ? (
                    <Link href={step.href} className="font-medium underline-offset-4 hover:underline">
                      {step.title}
                    </Link>
                  ) : (
                    <span className={step.done ? "text-muted-foreground line-through" : "font-medium"}>
                      {step.title}
                    </span>
                  )}
                  {step.note ? (
                    <Badge variant="outline" className="ml-auto">
                      {step.note}
                    </Badge>
                  ) : null}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle><h2>Your role</h2></CardTitle>
            <CardDescription>{ROLE_LABELS[organization.role]}</CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {ROLE_DESCRIPTIONS[organization.role]}.
          </CardContent>
        </Card>
      </div>
    </>
  );
}
