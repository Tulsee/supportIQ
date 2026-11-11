"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, MailIcon } from "lucide-react";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, api, errorMessage, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROLES, ROLE_DESCRIPTIONS, ROLE_LABELS } from "@/lib/roles";

const schema = z.object({
  email: z.email("Enter a valid email address"),
  role: z.enum(["admin", "agent", "customer"]),
});
type InviteValues = z.infer<typeof schema>;

export function InviteDialog() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const form = useForm<InviteValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", role: "agent" },
  });
  const { errors, isSubmitting } = form.formState;
  const role = useWatch({ control: form.control, name: "role" });

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await unwrap(
        api.POST("/api/v1/organizations/{org_id}/invitations", {
          params: { path: { org_id: organization.id } },
          body: values,
        }),
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.invitations(organization.id) });
      toast.success(`Invitation sent to ${values.email}`);
      form.reset();
      setOpen(false);
    } catch (error) {
      if (error instanceof ApiError && error.code === "already_member") {
        form.setError("email", { message: "This person is already a member." });
      } else {
        form.setError("root", { message: errorMessage(error) });
      }
    }
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) form.reset();
      }}
    >
      <DialogTrigger render={<Button />}>
        <MailIcon aria-hidden />
        Invite member
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={onSubmit} noValidate>
          <DialogHeader>
            <DialogTitle>Invite a member</DialogTitle>
            <DialogDescription>
              They&apos;ll get an email with a link to join {organization.name}. The link expires in 7 days.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="py-6">
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="invite-email">Email</FieldLabel>
              <Input
                id="invite-email"
                type="email"
                placeholder="name@company.com"
                aria-invalid={!!errors.email}
                {...form.register("email")}
              />
              <FieldError errors={[errors.email]} />
            </Field>
            <Field>
              <FieldLabel htmlFor="invite-role">Role</FieldLabel>
              <Controller
                control={form.control}
                name="role"
                render={({ field }) => (
                  <Select items={ROLE_LABELS} value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="invite-role" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES.map((value) => (
                        <SelectItem key={value} value={value}>
                          {ROLE_LABELS[value]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldDescription>{ROLE_DESCRIPTIONS[role]}.</FieldDescription>
            </Field>
            {errors.root ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Send invitation
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
