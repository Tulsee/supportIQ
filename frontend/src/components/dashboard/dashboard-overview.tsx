"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleCheckIcon, CircleIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { CustomerTickets } from "@/components/tickets/customer-tickets";
import { NewRequestDialog } from "@/components/tickets/new-request-dialog";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIVE_QUERY } from "@/components/tickets/hooks";
import { api, tenant, unwrap } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/lib/roles";
import { ACTIVE_STATUSES } from "@/lib/tickets";

type Step = { title: string; done: boolean; href?: string; note?: string };

function firstName(fullName: string): string {
  return fullName.split(" ")[0];
}

/** Customers: their requests and a way to ask for help. */
function CustomerHome() {
  const user = useCurrentUser();
  const organization = useActiveOrganization();
  return (
    <>
      <PageHeader
        title={`Hi, ${firstName(user.full_name)}`}
        description={`Get help from ${organization.name}.`}
        actions={<NewRequestDialog />}
      />
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">Recent requests</h2>
          <Link href="/tickets" className="text-sm font-medium hover:underline">
            View all
          </Link>
        </div>
        <CustomerTickets limit={5} compact />
      </div>
    </>
  );
}

function Stat({ label, value, href }: { label: string; value?: number; href: string }) {
  return (
    <Link href={href} className="block rounded-xl focus-visible:outline-2 focus-visible:outline-ring">
      <Card size="sm" className="transition-colors hover:bg-muted/40">
        <CardContent>
          <div className="text-sm text-muted-foreground">{label}</div>
          {value === undefined ? (
            <Skeleton className="mt-1 h-8 w-12" />
          ) : (
            <div className="text-3xl font-semibold tabular-nums">{value}</div>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}

function StaffDashboard() {
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
  });
  const counts = useQuery({
    queryKey: queryKeys.ticketCounts(organization.id),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets/counts", { params: { header: tenant(organization.id) } })),
  });

  const indexedDocuments = useQuery({
    queryKey: queryKeys.documents(organization.id, { status: ["indexed"], limit: 1 }),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents", {
          params: { query: { status: ["indexed"], limit: 1 }, header: tenant(organization.id) },
        }),
      ),
  });

  const byStatus = counts.data?.by_status;
  const active = byStatus ? ACTIVE_STATUSES.reduce((sum, s) => sum + byStatus[s], 0) : undefined;
  const total = byStatus ? Object.values(byStatus).reduce((sum, n) => sum + n, 0) : 0;

  const steps: Step[] = [
    { title: "Create your organization", done: true },
    {
      title: "Invite your team",
      done: (members.data?.total ?? 0) > 1,
      href: isAdmin ? "/settings/members" : undefined,
    },
    { title: "Log your first ticket", done: total > 0, href: "/tickets" },
    {
      title: "Upload knowledge base documents",
      done: (indexedDocuments.data?.total ?? 0) > 0,
      href: "/knowledge-base",
    },
    { title: "Answer customer questions with cited AI", done: false, note: "Coming soon" },
  ];

  return (
    <>
      <PageHeader
        title={`Welcome, ${firstName(user.full_name)}`}
        description={
          <>
            {organization.name} · <Badge variant="secondary">{ROLE_LABELS[organization.role]}</Badge>
          </>
        }
      />
      <div className="mb-4 grid gap-4 sm:grid-cols-3">
        <Stat label="Active tickets" value={active} href="/tickets" />
        <Stat label="Unassigned" value={counts.data?.unassigned} href="/tickets" />
        <Stat label="Assigned to you" value={counts.data?.assigned_to_me} href="/tickets" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>
              <h2>Getting started</h2>
            </CardTitle>
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
            <CardTitle>
              <h2>Your role</h2>
            </CardTitle>
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

export function DashboardOverview() {
  const organization = useActiveOrganization();
  return organization.role === "customer" ? <CustomerHome /> : <StaffDashboard />;
}
