"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { toast } from "sonner";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type NotificationDelivery,
  type NotificationKind,
  type Organization,
} from "@/lib/api/client";
import { DELIVERIES, DELIVERY_LABELS, KIND_LABELS } from "@/lib/notifications";
import { useOrganizations } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

function Preferences({ organization }: { organization: Organization }) {
  const queryClient = useQueryClient();
  const uid = useId();
  const key = queryKeys.notificationPreferences(organization.id);
  const params = { header: tenant(organization.id) };

  const preferences = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/notifications/preferences", { params })),
  });
  const change = useMutation({
    mutationFn: (body: { kind: NotificationKind; delivery: NotificationDelivery }) =>
      unwrap(api.PUT("/api/v1/notifications/preferences", { params, body })),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      toast.success("Notification preference saved");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (!preferences.data) return <Skeleton className="h-24 w-full" />;
  return (
    <ul className="divide-y">
      {preferences.data.items.map(({ kind, delivery }) => (
        <li key={kind} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <label htmlFor={`${uid}-${kind}`} className="text-sm font-medium">
              {KIND_LABELS[kind].label}
            </label>
            <p className="text-xs text-muted-foreground">{KIND_LABELS[kind].description}</p>
          </div>
          <Select
            items={DELIVERY_LABELS}
            value={delivery}
            onValueChange={(value) =>
              value && change.mutate({ kind, delivery: value as NotificationDelivery })
            }
          >
            <SelectTrigger id={`${uid}-${kind}`} className="w-full sm:w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DELIVERIES.map((value) => (
                <SelectItem key={value} value={value}>
                  {DELIVERY_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </li>
      ))}
    </ul>
  );
}

/** How the user hears about things in the active organization. */
export function NotificationsCard() {
  const { activeOrganization } = useOrganizations();
  if (!activeOrganization) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Notifications</h2>
        </CardTitle>
        <CardDescription>
          What you hear about in {activeOrganization.name}, and whether it&apos;s also emailed to you.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Preferences organization={activeOrganization} />
      </CardContent>
    </Card>
  );
}
