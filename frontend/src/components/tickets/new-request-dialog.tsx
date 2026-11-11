"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { refreshTickets } from "@/components/tickets/hooks";
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
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";

const schema = z.object({
  subject: z.string().trim().min(1, "Add a short summary").max(300),
  message: z.string().trim().min(1, "Tell us what you need").max(20_000),
});
type Values = z.infer<typeof schema>;
const EMPTY: Values = { subject: "", message: "" };

/** Customers: ask the organization for help. */
export function NewRequestDialog() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: EMPTY });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      const ticket = await unwrap(
        api.POST("/api/v1/tickets", {
          params: { header: tenant(organization.id) },
          body: values,
        }),
      );
      await refreshTickets(queryClient, organization.id);
      toast.success("Request sent. We'll get back to you here.");
      setOpen(false);
      form.reset(EMPTY);
      router.push(`/tickets/${ticket.id}`);
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <PlusIcon aria-hidden />
        New request
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={onSubmit} noValidate className="grid gap-6">
          <DialogHeader>
            <DialogTitle>How can we help?</DialogTitle>
            <DialogDescription>
              Describe your question or problem. {organization.name} will reply here.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field data-invalid={!!errors.subject}>
              <FieldLabel htmlFor="request-subject">Subject</FieldLabel>
              <Input
                id="request-subject"
                placeholder="e.g. I was charged twice"
                aria-invalid={!!errors.subject}
                {...form.register("subject")}
              />
              <FieldError errors={[errors.subject]} />
            </Field>
            <Field data-invalid={!!errors.message}>
              <FieldLabel htmlFor="request-message">Details</FieldLabel>
              <Textarea
                id="request-message"
                rows={6}
                aria-invalid={!!errors.message}
                {...form.register("message")}
              />
              <FieldError errors={[errors.message]} />
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
              Send request
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
