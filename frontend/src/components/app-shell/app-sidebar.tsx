"use client";

import {
  BookOpenIcon,
  Building2Icon,
  ChartLineIcon,
  LayoutDashboardIcon,
  ScanSearchIcon,
  TicketIcon,
  UserCogIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { OrgSwitcher } from "@/components/app-shell/org-switcher";
import { UserMenu } from "@/components/app-shell/user-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

type NavItem = { title: string; icon: LucideIcon; href?: string };

// Items without an href are upcoming modules, shown so the product shape is visible.
const WORKSPACE: NavItem[] = [
  { title: "Dashboard", icon: LayoutDashboardIcon, href: "/dashboard" },
  { title: "Tickets", icon: TicketIcon },
  { title: "Customers", icon: UsersIcon },
  { title: "Knowledge base", icon: BookOpenIcon },
  { title: "AI inspector", icon: ScanSearchIcon },
  { title: "Analytics", icon: ChartLineIcon },
];

const SETTINGS: NavItem[] = [
  { title: "Organization", icon: Building2Icon, href: "/settings" },
  { title: "Members", icon: UserCogIcon, href: "/settings/members" },
];

function NavGroup({ label, items }: { label: string; items: NavItem[] }) {
  const pathname = usePathname();
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map(({ title, icon: Icon, href }) => (
            <SidebarMenuItem key={title}>
              {href ? (
                <SidebarMenuButton
                  isActive={pathname === href}
                  tooltip={title}
                  render={<Link href={href} />}
                >
                  <Icon aria-hidden />
                  <span>{title}</span>
                </SidebarMenuButton>
              ) : (
                <>
                  <SidebarMenuButton disabled tooltip={`${title} (coming soon)`}>
                    <Icon aria-hidden />
                    <span>{title}</span>
                  </SidebarMenuButton>
                  <SidebarMenuBadge className="text-[10px] opacity-60">Soon</SidebarMenuBadge>
                </>
              )}
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

export function AppSidebar() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        <NavGroup label="Workspace" items={WORKSPACE} />
        <NavGroup label="Settings" items={SETTINGS} />
      </SidebarContent>
      <SidebarFooter>
        <UserMenu />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
