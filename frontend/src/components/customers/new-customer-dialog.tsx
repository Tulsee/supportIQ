"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
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
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

export const customerSchema = z.object({
  name: z.string().trim().min(1, "Enter a name").max(200),
  email: z.union([z.literal(""), z.email("Enter a valid email address")]),
});
export type CustomerValues = z.infer<typeof customerSchema>;
const EMPTY: CustomerValues = { name: "", email: "" };

export function NewCustomerDialog() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const form = useForm<CustomerValues>({
    resolver: zodResolver(customerSchema),
    defaultValues: EMPTY,
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async ({ name, email }) => {
    try {
      const customer = await unwrap(
        api.POST("/api/v1/customers", {
          params: { header: tenant(organization.id) },
          body: { name, email: email || null },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.customers(organization.id) });
      toast.success(`${customer.name} added`);
      setOpen(false);
      form.reset(EMPTY);
      router.push(`/customers/${customer.id}`);
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <PlusIcon aria-hidden />
        New customer
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={onSubmit} noValidate className="grid gap-6">
          <DialogHeader>
            <DialogTitle>New customer</DialogTitle>
            <DialogDescription>
              Someone you support. They don&apos;t need an account; invite them as a customer if
              they should follow their tickets online.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field data-invalid={!!errors.name}>
              <FieldLabel htmlFor="customer-name">Name</FieldLabel>
              <Input id="customer-name" aria-invalid={!!errors.name} {...form.register("name")} />
              <FieldError errors={[errors.name]} />
            </Field>
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="customer-email">Email</FieldLabel>
              <Input
                id="customer-email"
                type="email"
                aria-invalid={!!errors.email}
                {...form.register("email")}
              />
              <FieldDescription>
                Optional. If they sign up with this email, their account is linked automatically.
              </FieldDescription>
              <FieldError errors={[errors.email]} />
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
              Add customer
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
