"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { refreshTickets, useAssignableMembers } from "@/components/tickets/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type Customer,
  type TicketPriority,
} from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { PRIORITIES, PRIORITY_LABELS } from "@/lib/tickets";

const UNASSIGNED = "unassigned";

const schema = z
  .object({
    customerMode: z.enum(["existing", "new"]),
    customer: z.custom<Customer>().nullable(),
    newCustomerName: z.string().trim().max(200),
    newCustomerEmail: z.union([z.literal(""), z.email("Enter a valid email address")]),
    subject: z.string().trim().min(1, "Add a subject").max(300),
    message: z.string().trim().min(1, "Describe the request").max(20_000),
    priority: z.enum(["low", "normal", "high", "urgent"]),
    assignee: z.string(),
  })
  .superRefine((values, ctx) => {
    if (values.customerMode === "existing" && !values.customer) {
      ctx.addIssue({ code: "custom", path: ["customer"], message: "Choose a customer" });
    }
    if (values.customerMode === "new" && !values.newCustomerName) {
      ctx.addIssue({ code: "custom", path: ["newCustomerName"], message: "Enter a name" });
    }
  });
type Values = z.infer<typeof schema>;

/** Staff: open a ticket on a customer's behalf (e.g. from a call or email). */
export function NewTicketDialog({
  customer: presetCustomer,
  trigger,
}: {
  /** Open the dialog for this customer (customer detail page). */
  customer?: Customer;
  trigger?: React.ReactElement;
}) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();
  const { members } = useAssignableMembers();
  const [open, setOpen] = useState(false);
  const [customerQuery, setCustomerQuery] = useState("");
  const search = useDebouncedValue(customerQuery.trim());

  const defaults: Values = {
    customerMode: "existing",
    customer: presetCustomer ?? null,
    newCustomerName: "",
    newCustomerEmail: "",
    subject: "",
    message: "",
    priority: "normal",
    assignee: UNASSIGNED,
  };
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults });
  const { errors, isSubmitting } = form.formState;
  const customerMode = useWatch({ control: form.control, name: "customerMode" });

  const page = { limit: 20, search: search || undefined };
  const customers = useQuery({
    queryKey: queryKeys.customers(organization.id, page),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/customers", {
          params: { query: page, header: tenant(organization.id) },
        }),
      ),
    enabled: open && !presetCustomer && customerMode === "existing",
    placeholderData: keepPreviousData,
  });

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      let customerId = values.customer?.id;
      if (values.customerMode === "new") {
        const created = await unwrap(
          api.POST("/api/v1/customers", {
            params: { header: tenant(organization.id) },
            body: { name: values.newCustomerName, email: values.newCustomerEmail || null },
          }),
        );
        customerId = created.id;
        void queryClient.invalidateQueries({ queryKey: queryKeys.customers(organization.id) });
      }
      const ticket = await unwrap(
        api.POST("/api/v1/tickets", {
          params: { header: tenant(organization.id) },
          body: {
            customer_id: customerId,
            subject: values.subject,
            message: values.message,
            priority: values.priority,
            assignee_id: values.assignee === UNASSIGNED ? null : values.assignee,
          },
        }),
      );
      await refreshTickets(queryClient, organization.id);
      toast.success(`Ticket #${ticket.number} created`);
      setOpen(false);
      form.reset(defaults);
      router.push(`/tickets/${ticket.id}`);
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  const assigneeItems: Record<string, string> = {
    [UNASSIGNED]: "Unassigned",
    ...Object.fromEntries(members.map((m) => [m.user_id, m.full_name])),
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) form.reset(defaults);
      }}
    >
      <DialogTrigger
        render={
          trigger ?? (
            <Button>
              <PlusIcon aria-hidden />
              New ticket
            </Button>
          )
        }
      />
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={onSubmit} noValidate className="grid gap-6">
          <DialogHeader>
            <DialogTitle>New ticket</DialogTitle>
            <DialogDescription>
              Log a request on a customer&apos;s behalf, for example from a call or an email.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            {presetCustomer ? (
              <Field>
                <FieldLabel>Customer</FieldLabel>
                <p className="text-sm">
                  {presetCustomer.name}
                  {presetCustomer.email ? (
                    <span className="text-muted-foreground"> · {presetCustomer.email}</span>
                  ) : null}
                </p>
              </Field>
            ) : customerMode === "existing" ? (
              <Field data-invalid={!!errors.customer}>
                <div className="flex items-center justify-between">
                  <FieldLabel htmlFor="ticket-customer">Customer</FieldLabel>
                  <Button
                    type="button"
                    variant="link"
                    size="xs"
                    className="h-auto p-0"
                    onClick={() => {
                      form.setValue("customerMode", "new");
                      form.setValue("newCustomerName", customerQuery.trim());
                      form.clearErrors("customer");
                    }}
                  >
                    New customer
                  </Button>
                </div>
                <Controller
                  control={form.control}
                  name="customer"
                  render={({ field }) => (
                    <Combobox<Customer>
                      items={customers.data?.items ?? []}
                      filter={null}
                      value={field.value}
                      onValueChange={(value) => field.onChange(value ?? null)}
                      inputValue={customerQuery}
                      onInputValueChange={setCustomerQuery}
                      itemToStringLabel={(c) => c.name}
                      isItemEqualToValue={(a, b) => a.id === b.id}
                    >
                      <ComboboxInput
                        id="ticket-customer"
                        placeholder="Search customers by name or email"
                        aria-invalid={!!errors.customer}
                        className="w-full"
                      />
                      <ComboboxContent>
                        <ComboboxEmpty>
                          {customers.isFetching ? "Searching…" : "No customers found."}
                        </ComboboxEmpty>
                        <ComboboxList>
                          {(customer: Customer) => (
                            <ComboboxItem key={customer.id} value={customer}>
                              <span className="truncate">{customer.name}</span>
                              {customer.email ? (
                                <span className="truncate text-xs text-muted-foreground">
                                  {customer.email}
                                </span>
                              ) : null}
                            </ComboboxItem>
                          )}
                        </ComboboxList>
                      </ComboboxContent>
                    </Combobox>
                  )}
                />
                <FieldError errors={[errors.customer]} />
              </Field>
            ) : (
              <div className="grid gap-4 rounded-lg border p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">New customer</span>
                  <Button
                    type="button"
                    variant="link"
                    size="xs"
                    className="h-auto p-0"
                    onClick={() => form.setValue("customerMode", "existing")}
                  >
                    Choose existing
                  </Button>
                </div>
                <Field data-invalid={!!errors.newCustomerName}>
                  <FieldLabel htmlFor="new-customer-name">Name</FieldLabel>
                  <Input
                    id="new-customer-name"
                    aria-invalid={!!errors.newCustomerName}
                    {...form.register("newCustomerName")}
                  />
                  <FieldError errors={[errors.newCustomerName]} />
                </Field>
                <Field data-invalid={!!errors.newCustomerEmail}>
                  <FieldLabel htmlFor="new-customer-email">Email (optional)</FieldLabel>
                  <Input
                    id="new-customer-email"
                    type="email"
                    aria-invalid={!!errors.newCustomerEmail}
                    {...form.register("newCustomerEmail")}
                  />
                  <FieldError errors={[errors.newCustomerEmail]} />
                </Field>
              </div>
            )}
            <Field data-invalid={!!errors.subject}>
              <FieldLabel htmlFor="ticket-subject">Subject</FieldLabel>
              <Input
                id="ticket-subject"
                aria-invalid={!!errors.subject}
                {...form.register("subject")}
              />
              <FieldError errors={[errors.subject]} />
            </Field>
            <Field data-invalid={!!errors.message}>
              <FieldLabel htmlFor="ticket-message">Message</FieldLabel>
              <Textarea
                id="ticket-message"
                rows={5}
                placeholder="What does the customer need?"
                aria-invalid={!!errors.message}
                {...form.register("message")}
              />
              <FieldError errors={[errors.message]} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor="ticket-priority">Priority</FieldLabel>
                <Controller
                  control={form.control}
                  name="priority"
                  render={({ field }) => (
                    <Select
                      items={PRIORITY_LABELS}
                      value={field.value}
                      onValueChange={(value) => value && field.onChange(value as TicketPriority)}
                    >
                      <SelectTrigger id="ticket-priority" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {PRIORITIES.map((priority) => (
                          <SelectItem key={priority} value={priority}>
                            {PRIORITY_LABELS[priority]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="ticket-assignee">Assignee</FieldLabel>
                <Controller
                  control={form.control}
                  name="assignee"
                  render={({ field }) => (
                    <Select
                      items={assigneeItems}
                      value={field.value}
                      onValueChange={(value) => value && field.onChange(value)}
                    >
                      <SelectTrigger id="ticket-assignee" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(assigneeItems).map(([id, name]) => (
                          <SelectItem key={id} value={id}>
                            {name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                />
              </Field>
            </div>
            {errors.root ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Create ticket
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
