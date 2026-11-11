"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { refreshOrganizations, rememberActiveOrganization } from "@/lib/org/org-provider";

const schema = z.object({
  name: z.string().trim().min(2, "Use at least 2 characters").max(200),
});
type OrgValues = z.infer<typeof schema>;

export function CreateOrganizationForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const form = useForm<OrgValues>({ resolver: zodResolver(schema), defaultValues: { name: "" } });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async ({ name }) => {
    try {
      const organization = await unwrap(api.POST("/api/v1/organizations", { body: { name } }));
      rememberActiveOrganization(organization.id);
      await refreshOrganizations(queryClient);
      toast.success(`${organization.name} is ready`);
      form.reset();
      router.replace("/dashboard");
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate>
      <FieldGroup>
        <Field data-invalid={!!errors.name}>
          <FieldLabel htmlFor="org-name">Organization name</FieldLabel>
          <Input
            id="org-name"
            placeholder="Acme Support"
            autoComplete="organization"
            aria-invalid={!!errors.name}
            {...form.register("name")}
          />
          <FieldDescription>Usually your company or team name. You can change it later.</FieldDescription>
          <FieldError errors={[errors.name]} />
        </Field>
        {errors.root ? (
          <Alert variant="destructive">
            <AlertDescription>{errors.root.message}</AlertDescription>
          </Alert>
        ) : null}
        <Button type="submit" size="lg" disabled={isSubmitting}>
          {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
          Create organization
        </Button>
      </FieldGroup>
    </form>
  );
}
