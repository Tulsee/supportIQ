"use client";

import type { ReactNode } from "react";

import { AppSidebar } from "@/components/app-shell/app-sidebar";
import { AuthGuard } from "@/components/guards";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { OrgProvider, useOrganizations } from "@/lib/org/org-provider";

function TopBar() {
  const { activeOrganization } = useOrganizations();
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 h-4" />
      <span className="truncate text-sm text-muted-foreground">{activeOrganization?.name}</span>
    </header>
  );
}

/** Signed-in application shell: auth gate, organizations, sidebar and top bar. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <OrgProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            <TopBar />
            <div className="mx-auto w-full max-w-6xl flex-1 p-6">{children}</div>
          </SidebarInset>
        </SidebarProvider>
      </OrgProvider>
    </AuthGuard>
  );
}
