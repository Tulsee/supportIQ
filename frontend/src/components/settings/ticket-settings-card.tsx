"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";

const schema = z.object({
  auto_close_after_days: z.coerce
    .number({ error: "Enter a number of days" })
    .int("Use whole days")
    .min(0, "Use 0 to turn it off")
    .max(90, "At most 90 days"),
});
type Input = z.input<typeof schema>;
type Values = z.output<typeof schema>;

/** Ticket lifecycle options: auto-closing resolved tickets. */
export function TicketSettingsCard() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const isAdmin = organization.role === "admin";
  const days = organization.settings.auto_close_after_days;
  const form = useForm<Input, unknown, Values>({
    resolver: zodResolver(schema),
    defaultValues: { auto_close_after_days: days },
  });
  const { errors, isDirty } = form.formState;

  useEffect(() => form.reset({ auto_close_after_days: days }), [organization.id, days, form]);

  const save = useMutation({
    mutationFn: (values: Values) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}", {
          params: { path: { org_id: organization.id } },
          body: { settings: values },
        }),
      ),
    onSuccess: async (updated) => {
      await refreshOrganizations(queryClient);
      toast.success("Ticket settings saved");
      form.reset({ auto_close_after_days: updated.settings.auto_close_after_days });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card className="max-w-2xl">
      <form onSubmit={form.handleSubmit((values) => save.mutate(values))} noValidate>
        <CardHeader>
          <CardTitle>
            <h2>Tickets</h2>
          </CardTitle>
          <CardDescription>
            {isAdmin ? "How tickets move through their lifecycle." : "Only admins can change these settings."}
          </CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <Field data-invalid={!!errors.auto_close_after_days} data-disabled={!isAdmin}>
            <FieldLabel htmlFor="auto-close-days">Close resolved tickets after</FieldLabel>
            <div className="flex items-center gap-2">
              <Input
                id="auto-close-days"
                type="number"
                inputMode="numeric"
                min={0}
                max={90}
                className="w-24"
                disabled={!isAdmin}
                aria-invalid={!!errors.auto_close_after_days}
                aria-describedby="auto-close-days-help"
                {...form.register("auto_close_after_days")}
              />
              <span className="text-sm text-muted-foreground">days without a reply</span>
            </div>
            <FieldDescription id="auto-close-days-help">
              A customer reply reopens a resolved ticket, so only tickets nobody replied to are
              closed. 0 turns this off.
            </FieldDescription>
            <FieldError errors={[errors.auto_close_after_days]} />
          </Field>
        </CardContent>
        {isAdmin ? (
          <CardFooter className="justify-end border-t">
            <Button type="submit" disabled={!isDirty || save.isPending}>
              {save.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Save changes
            </Button>
          </CardFooter>
        ) : null}
      </form>
    </Card>
  );
}
