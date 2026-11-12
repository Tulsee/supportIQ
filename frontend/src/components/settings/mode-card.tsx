"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage, unwrap, type Schemas } from "@/lib/api/client";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";
import { cn } from "@/lib/utils";

type Settings = Schemas["OrganizationSettingsUpdate"];
type Mode = "off" | "shadow" | "on";

/** An off / shadow / on organization setting, saved as soon as it's picked. */
export function ModeCard({
  title,
  description,
  setting,
  modes,
}: {
  title: string;
  description: string;
  setting: "routing_mode" | "triage_mode";
  modes: { value: Mode; label: string; description: string }[];
}) {
  const titleId = useId();
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const current = organization.settings[setting];
  const save = useMutation({
    mutationFn: (value: Mode) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}", {
          params: { path: { org_id: organization.id } },
          body: { settings: { [setting]: value } as Settings },
        }),
      ),
    onSuccess: async (updated) => {
      await refreshOrganizations(queryClient);
      const mode = modes.find((m) => m.value === updated.settings[setting]);
      toast.success(`${title}: ${mode?.label.toLowerCase()}`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>
          <h2 id={titleId}>{title}</h2>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div role="radiogroup" aria-labelledby={titleId} className="grid gap-3 sm:grid-cols-3">
          {modes.map((mode) => {
            const checked = current === mode.value;
            return (
              <button
                key={mode.value}
                type="button"
                role="radio"
                aria-checked={checked}
                disabled={save.isPending}
                onClick={() => !checked && save.mutate(mode.value)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60",
                  checked ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:bg-muted/50",
                )}
              >
                <span className="flex items-center gap-2 text-sm font-medium">
                  <span
                    className={cn(
                      "flex size-4 items-center justify-center rounded-full border",
                      checked ? "border-primary" : "border-muted-foreground/50",
                    )}
                    aria-hidden
                  >
                    {checked ? <span className="size-2 rounded-full bg-primary" /> : null}
                  </span>
                  {mode.label}
                  {save.isPending && save.variables === mode.value ? (
                    <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
                  ) : null}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">{mode.description}</span>
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
