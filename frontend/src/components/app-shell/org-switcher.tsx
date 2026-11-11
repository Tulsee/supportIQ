"use client";

import { CheckIcon, ChevronsUpDownIcon, PlusIcon } from "lucide-react";
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
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { useActiveOrganization, useOrganizations } from "@/lib/org/org-provider";
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
  const { organizations, selectOrganization } = useOrganizations();
  const active = useActiveOrganization();

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
