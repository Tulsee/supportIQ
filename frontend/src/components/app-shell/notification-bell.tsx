"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellIcon, CheckCheckIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, tenant, unwrap, type AppNotification, type Organization } from "@/lib/api/client";
import { queryKeys } from "@/lib/query-keys";
import { timeAgo } from "@/lib/tickets";
import { cn } from "@/lib/utils";

const RECENT = 15;

/** The bell in the top bar: recent notifications in this organization, live. */
export function NotificationBell({ organization }: { organization: Organization }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const orgId = organization.id;
  const headers = { header: tenant(orgId) };

  const list = useQuery({
    queryKey: queryKeys.notifications(orgId, { limit: RECENT }),
    queryFn: () =>
      unwrap(api.GET("/api/v1/notifications", { params: { ...headers, query: { limit: RECENT } } })),
  });
  const markRead = useMutation({
    mutationFn: (body: { ids?: string[]; all: boolean }) =>
      unwrap(api.POST("/api/v1/notifications/read", { params: headers, body })),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications(orgId), refetchType: "all" }),
  });

  const unread = list.data?.unread ?? 0;
  const items = list.data?.items ?? [];

  function open(item: AppNotification) {
    if (!item.read_at) markRead.mutate({ ids: [item.id], all: false });
    if (item.ticket_id) router.push(`/tickets/${item.ticket_id}`);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className="relative"
            aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
          />
        }
      >
        <BellIcon aria-hidden />
        {unread ? (
          <span
            className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] leading-none font-semibold text-primary-foreground tabular-nums"
            aria-hidden
          >
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 max-w-[calc(100vw-2rem)]">
        <DropdownMenuGroup>
          <div className="flex items-center justify-between gap-2 px-1.5 py-1">
            <DropdownMenuLabel className="p-0 text-sm font-medium text-foreground">
              Notifications
            </DropdownMenuLabel>
            {unread ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => markRead.mutate({ all: true })}
                disabled={markRead.isPending}
              >
                <CheckCheckIcon aria-hidden />
                Mark all read
              </Button>
            ) : null}
          </div>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        {items.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-muted-foreground">
            {list.isPending ? "Loading…" : "You're all caught up."}
          </p>
        ) : (
          <DropdownMenuGroup>
            {items.map((item) => (
              <DropdownMenuItem
                key={item.id}
                onClick={() => open(item)}
                className="flex-col items-start gap-0.5 px-2 py-2"
              >
                <span className="flex w-full items-start gap-2">
                  <span
                    className={cn(
                      "mt-1.5 size-2 shrink-0 rounded-full",
                      item.read_at ? "bg-transparent" : "bg-primary",
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1">
                    <span className={cn("block text-sm", !item.read_at && "font-medium")}>
                      {item.title}
                      {item.read_at ? null : <span className="sr-only"> (unread)</span>}
                    </span>
                    {item.body ? (
                      <span className="line-clamp-2 block text-xs text-muted-foreground">
                        {item.body}
                      </span>
                    ) : null}
                    <span className="block text-xs text-muted-foreground">
                      {timeAgo(item.created_at)}
                    </span>
                  </span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
