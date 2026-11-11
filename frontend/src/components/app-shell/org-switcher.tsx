"use client";

import { CheckIcon, ChevronsUpDownIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
} from "@/components/ui/sidebar";
import { useOrganizations } from "@/lib/org/org-provider";
import { ROLE_LABELS, initials } from "@/lib/roles";

function OrgAvatar({ name }: { name: string }) {
  return (
    <span className="flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-xs font-semibold text-sidebar-primary-foreground">
      {initials(name)}
    </span>
  );
}

export function OrgSwitcher() {
  const router = useRouter();
  const { organizations, activeOrganization: active, isLoading, selectOrganization } =
    useOrganizations();

  if (!active) {
    // Loading, or the user (e.g. a platform admin) belongs to no organization yet.
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          {isLoading ? (
            <SidebarMenuSkeleton showIcon />
          ) : (
            <SidebarMenuButton size="lg" render={<Link href="/onboarding" />}>
              <span className="flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg border border-dashed border-sidebar-foreground/40">
                <PlusIcon className="size-4" aria-hidden />
              </span>
              <span className="truncate font-medium">Create organization</span>
            </SidebarMenuButton>
          )}
        </SidebarMenuItem>
      </SidebarMenu>
    );
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton size="lg" className="data-popup-open:bg-sidebar-accent" />
            }
          >
            <OrgAvatar name={active.name} />
            <span className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-semibold">{active.name}</span>
              <span className="truncate text-xs opacity-70">{ROLE_LABELS[active.role]}</span>
            </span>
            <ChevronsUpDownIcon className="ml-auto" aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent className="min-w-60" align="start" side="bottom" sideOffset={4}>
            <DropdownMenuGroup>
              <DropdownMenuLabel>Organizations</DropdownMenuLabel>
              {organizations.map((org) => (
                <DropdownMenuItem key={org.id} onClick={() => selectOrganization(org.id)}>
                  <span className="flex-1 truncate">{org.name}</span>
                  {org.id === active.id ? <CheckIcon aria-label="Current" /> : null}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => router.push("/onboarding")}>
              <PlusIcon aria-hidden />
              Create organization
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
