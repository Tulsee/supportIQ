"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronsUpDownIcon,
  CircleUserIcon,
  LogOutIcon,
  MonitorIcon,
  MoonIcon,
  SunIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { api, errorMessage, tenant, unwrap, type AgentStatus } from "@/lib/api/client";
import { useAuth, useCurrentUser } from "@/lib/auth/auth-provider";
import { useOrganizations } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { initials } from "@/lib/roles";
import { cn } from "@/lib/utils";

const STATUSES: { value: AgentStatus; label: string; dot: string }[] = [
  { value: "available", label: "Available", dot: "bg-emerald-500" },
  { value: "busy", label: "Busy", dot: "bg-amber-500" },
  { value: "away", label: "Away", dot: "bg-muted-foreground" },
];

/**
 * The signed-in staff member's status in the active organization. Routing only
 * gives tickets to agents who are available and have the app open.
 */
function useOwnStatus(orgId: string | undefined) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.agentMe(orgId ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/agents/me", { params: { header: tenant(orgId ?? "") } })),
    enabled: Boolean(orgId),
  });
  const mutation = useMutation({
    mutationFn: (status: AgentStatus) =>
      unwrap(
        api.PUT("/api/v1/agents/me/presence", {
          params: { header: tenant(orgId ?? "") },
          body: { status },
        }),
      ),
    onSuccess: (agent) => {
      if (!orgId) return;
      queryClient.setQueryData(queryKeys.agentMe(orgId), agent);
      void queryClient.invalidateQueries({ queryKey: queryKeys.agents(orgId), refetchType: "all" });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return { status: query.data?.status, setStatus: mutation.mutate };
}

export function UserMenu() {
  const user = useCurrentUser();
  const { logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const { activeOrganization } = useOrganizations();
  const staff = activeOrganization && activeOrganization.role !== "customer";
  const { status, setStatus } = useOwnStatus(staff ? activeOrganization.id : undefined);
  const current = STATUSES.find((s) => s.value === status);

  const signOut = async () => {
    await logout();
    router.replace("/login");
  };

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton size="lg" className="data-popup-open:bg-sidebar-accent" />
            }
          >
            <span className="relative flex size-8 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-xs font-semibold">
              {initials(user.full_name)}
              {current ? (
                <span
                  className={cn(
                    "absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full ring-2 ring-sidebar",
                    current.dot,
                  )}
                  aria-hidden
                />
              ) : null}
            </span>
            <span className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">
                {user.full_name}
                {current ? <span className="sr-only">, {current.label}</span> : null}
              </span>
              <span className="truncate text-xs opacity-70">{user.email}</span>
            </span>
            <ChevronsUpDownIcon className="ml-auto" aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent className="min-w-56" align="end" side="top" sideOffset={4}>
            <DropdownMenuItem onClick={() => router.push("/account")}>
              <CircleUserIcon aria-hidden />
              Account settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {staff && status ? (
              <>
                <DropdownMenuGroup>
                  <DropdownMenuLabel>Status</DropdownMenuLabel>
                  <DropdownMenuRadioGroup
                    value={status}
                    onValueChange={(value) => setStatus(value as AgentStatus)}
                  >
                    {STATUSES.map((s) => (
                      <DropdownMenuRadioItem key={s.value} value={s.value}>
                        <span className={cn("size-2 rounded-full", s.dot)} aria-hidden />
                        {s.label}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
              </>
            ) : null}
            <DropdownMenuGroup>
              <DropdownMenuLabel>Theme</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={setTheme}>
                <DropdownMenuRadioItem value="light">
                  <SunIcon aria-hidden /> Light
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="dark">
                  <MoonIcon aria-hidden /> Dark
                </DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="system">
                  <MonitorIcon aria-hidden /> System
                </DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={signOut}>
              <LogOutIcon aria-hidden />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
