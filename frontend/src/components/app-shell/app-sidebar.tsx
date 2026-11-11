"use client";

import {
  BookOpenIcon,
  Building2Icon,
  BuildingIcon,
  ChartLineIcon,
  LayoutDashboardIcon,
  ScanSearchIcon,
  TicketIcon,
  UserCogIcon,
  UsersIcon,
  UsersRoundIcon,
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
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { useOrganizations } from "@/lib/org/org-provider";

type NavItem = {
  title: string;
  icon: LucideIcon;
  /** Omitted for upcoming modules, shown so the product shape is visible. */
  href?: string;
  /** Also highlight for nested pages (e.g. /tickets/123). */
  matchNested?: boolean;
};

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

const PLATFORM: NavItem[] = [
  { title: "All users", icon: UsersRoundIcon, href: "/admin/users" },
  { title: "All organizations", icon: BuildingIcon, href: "/admin/organizations" },
];

function isCurrent(pathname: string, { href, matchNested }: NavItem): boolean {
  if (!href) return false;
  return pathname === href || (!!matchNested && pathname.startsWith(`${href}/`));
}

function NavGroup({ label, items }: { label: string; items: NavItem[] }) {
  const pathname = usePathname();
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const { title, icon: Icon, href } = item;
            return (
              <SidebarMenuItem key={title}>
                {href ? (
                  <SidebarMenuButton
                    isActive={isCurrent(pathname, item)}
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
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

export function AppSidebar() {
  const user = useCurrentUser();
  const { activeOrganization } = useOrganizations();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        {activeOrganization ? (
          <>
            <NavGroup label="Workspace" items={WORKSPACE} />
            <NavGroup label="Settings" items={SETTINGS} />
          </>
        ) : null}
        {user.is_superadmin ? <NavGroup label="Platform" items={PLATFORM} /> : null}
      </SidebarContent>
      <SidebarFooter>
        <UserMenu />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
