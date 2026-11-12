"use client";

import {
  BookOpenIcon,
  Building2Icon,
  BuildingIcon,
  ChartLineIcon,
  LayoutDashboardIcon,
  MessageSquareTextIcon,
  ScanSearchIcon,
  ScrollTextIcon,
  TagsIcon,
  TimerIcon,
  TicketIcon,
  UserCogIcon,
  UsersIcon,
  UsersRoundIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

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
  { title: "Tickets", icon: TicketIcon, href: "/tickets", matchNested: true },
  { title: "Customers", icon: UsersIcon, href: "/customers", matchNested: true },
  { title: "Knowledge base", icon: BookOpenIcon, href: "/knowledge-base", matchNested: true },
  { title: "Ask AI", icon: MessageSquareTextIcon, href: "/ask" },
  { title: "AI inspector", icon: ScanSearchIcon, href: "/ai-inspector", matchNested: true },
  { title: "Analytics", icon: ChartLineIcon, href: "/analytics" },
];

// Customer members only use the portal.
const PORTAL: NavItem[] = [
  { title: "Home", icon: LayoutDashboardIcon, href: "/dashboard" },
  { title: "My requests", icon: TicketIcon, href: "/tickets", matchNested: true },
];

const SETTINGS: NavItem[] = [
  { title: "Organization", icon: Building2Icon, href: "/settings" },
  { title: "Members", icon: UserCogIcon, href: "/settings/members" },
];

const ADMIN_SETTINGS: NavItem[] = [
  ...SETTINGS,
  { title: "Categories", icon: TagsIcon, href: "/settings/categories" },
  { title: "Service levels", icon: TimerIcon, href: "/settings/sla" },
  { title: "Audit log", icon: ScrollTextIcon, href: "/settings/audit-log" },
];

const PLATFORM: NavItem[] = [
  { title: "All users", icon: UsersRoundIcon, href: "/admin/users" },
  { title: "All organizations", icon: BuildingIcon, href: "/admin/organizations" },
];

function isCurrent(pathname: string, { href, matchNested }: NavItem): boolean {
  if (!href) return false;
  return pathname === href || (!!matchNested && pathname.startsWith(`${href}/`));
}

function NavGroup({
  label,
  items,
  pathname,
}: {
  label: string;
  items: NavItem[];
  pathname: string | null;
}) {
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
                    isActive={pathname !== null && isCurrent(pathname, item)}
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

function NavGroups({ pathname }: { pathname: string | null }) {
  const user = useCurrentUser();
  const { activeOrganization } = useOrganizations();
  return (
    <>
      {activeOrganization?.role === "customer" ? (
        <NavGroup label="Support" items={PORTAL} pathname={pathname} />
      ) : activeOrganization ? (
        <>
          <NavGroup label="Workspace" items={WORKSPACE} pathname={pathname} />
          <NavGroup
            label="Settings"
            items={activeOrganization.role === "admin" ? ADMIN_SETTINGS : SETTINGS}
            pathname={pathname}
          />
        </>
      ) : null}
      {user.is_superadmin ? (
        <NavGroup label="Platform" items={PLATFORM} pathname={pathname} />
      ) : null}
    </>
  );
}

function CurrentNavGroups() {
  return <NavGroups pathname={usePathname()} />;
}

export function AppSidebar() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <OrgSwitcher />
      </SidebarHeader>
      <SidebarContent>
        {/* usePathname() suspends on dynamic routes (e.g. /tickets/[id]) under Cache
            Components; until it resolves, show the same links without highlighting. */}
        <Suspense fallback={<NavGroups pathname={null} />}>
          <CurrentNavGroups />
        </Suspense>
      </SidebarContent>
      <SidebarFooter>
        <UserMenu />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
