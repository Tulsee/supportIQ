"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PageHeader } from "@/components/page-header";
import { TicketSettingsCard } from "@/components/settings/ticket-settings-card";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";

const schema = z.object({ name: z.string().trim().min(2, "Use at least 2 characters").max(200) });
type Values = z.infer<typeof schema>;

export function OrganizationSettings() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const isAdmin = organization.role === "admin";
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { name: organization.name },
  });
  const { errors, isDirty } = form.formState;

  // Switching organizations shows the other organization's values.
  useEffect(() => form.reset({ name: organization.name }), [organization.id, organization.name, form]);

  const rename = useMutation({
    mutationFn: (values: Values) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}", {
          params: { path: { org_id: organization.id } },
          body: { name: values.name },
        }),
      ),
    onSuccess: async (updated) => {
      await refreshOrganizations(queryClient);
      toast.success("Organization updated");
      form.reset({ name: updated.name });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <>
      <PageHeader title="Organization" description="General settings for your organization." />
      <Card className="max-w-2xl">
        <form onSubmit={form.handleSubmit((values) => rename.mutate(values))} noValidate>
          <CardHeader>
            <CardTitle><h2>Profile</h2></CardTitle>
            <CardDescription>
              {isAdmin ? "Visible to everyone in the organization." : "Only admins can change these settings."}
            </CardDescription>
          </CardHeader>
          <CardContent className="py-6">
            <FieldGroup>
              <Field data-invalid={!!errors.name} data-disabled={!isAdmin}>
                <FieldLabel htmlFor="org-name">Name</FieldLabel>
                <Input
                  id="org-name"
                  disabled={!isAdmin}
                  aria-invalid={!!errors.name}
                  {...form.register("name")}
                />
                <FieldError errors={[errors.name]} />
              </Field>
              <Field data-disabled>
                <FieldLabel htmlFor="org-slug">Slug</FieldLabel>
                <Input id="org-slug" value={organization.slug} readOnly disabled className="font-mono" />
                <FieldDescription>A unique identifier for the organization.</FieldDescription>
              </Field>
            </FieldGroup>
          </CardContent>
          {isAdmin ? (
            <CardFooter className="justify-end border-t">
              <Button type="submit" disabled={!isDirty || rename.isPending}>
                {rename.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
                Save changes
              </Button>
            </CardFooter>
          ) : null}
        </form>
      </Card>
      <div className="mt-6">
        <TicketSettingsCard />
      </div>
    </>
  );
}
