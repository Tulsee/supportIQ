"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useId } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { customerSchema, type CustomerValues } from "@/components/customers/new-customer-dialog";
import { NewTicketDialog } from "@/components/tickets/new-ticket-dialog";
import { StatusBadge } from "@/components/tickets/ticket-badges";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { LIVE_QUERY } from "@/components/tickets/hooks";
import { ApiError, api, errorMessage, tenant, unwrap, type Customer } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDate } from "@/lib/roles";
import { timeAgo } from "@/lib/tickets";

function CustomerForm({ customer }: { customer: Customer }) {
  const uid = useId(); // unique per instance (visited routes stay mounted)
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const form = useForm<CustomerValues>({
    resolver: zodResolver(customerSchema),
    defaultValues: { name: customer.name, email: customer.email ?? "" },
  });
  const { errors, isDirty } = form.formState;
  useEffect(
    () => form.reset({ name: customer.name, email: customer.email ?? "" }),
    [customer.id, customer.name, customer.email, form],
  );

  const save = useMutation({
    mutationFn: ({ name, email }: CustomerValues) =>
      unwrap(
        api.PATCH("/api/v1/customers/{customer_id}", {
          params: { path: { customer_id: customer.id }, header: tenant(organization.id) },
          body: { name, email: email || null },
        }),
      ),
    onSuccess: async (updated) => {
      queryClient.setQueryData(queryKeys.customer(organization.id, customer.id), updated);
      await queryClient.invalidateQueries({ queryKey: queryKeys.customers(organization.id) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.tickets(organization.id) });
      toast.success("Customer updated");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card>
      <form onSubmit={form.handleSubmit((values) => save.mutate(values))} noValidate>
        <CardHeader>
          <CardTitle>
            <h2>Details</h2>
          </CardTitle>
          <CardDescription>Added {formatDate(customer.created_at)}.</CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Field data-invalid={!!errors.name}>
              <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
              <Input id={`${uid}-name`} aria-invalid={!!errors.name} {...form.register("name")} />
              <FieldError errors={[errors.name]} />
            </Field>
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor={`${uid}-email`}>Email</FieldLabel>
              <Input
                id={`${uid}-email`}
                type="email"
                aria-invalid={!!errors.email}
                {...form.register("email")}
              />
              <FieldError errors={[errors.email]} />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="justify-end border-t">
          <Button type="submit" disabled={!isDirty || save.isPending}>
            {save.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
            Save changes
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function CustomerTicketList({ customer }: { customer: Customer }) {
  const organization = useActiveOrganization();
  const query = { customer_id: customer.id, limit: 50 };
  const tickets = useQuery({
    queryKey: queryKeys.tickets(organization.id, query),
    ...LIVE_QUERY,
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets", { params: { query, header: tenant(organization.id) } })),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Tickets</h2>
        </CardTitle>
        <CardDescription>Everything {customer.name} has asked for.</CardDescription>
        <CardAction>
          <NewTicketDialog
            customer={customer}
            trigger={
              <Button variant="outline" size="sm">
                <PlusIcon aria-hidden />
                New ticket
              </Button>
            }
          />
        </CardAction>
      </CardHeader>
      <CardContent>
        {tickets.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : tickets.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(tickets.error)}</AlertDescription>
          </Alert>
        ) : tickets.data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No tickets yet.</p>
        ) : (
          <ul className="divide-y">
            {tickets.data.items.map((ticket) => (
              <li key={ticket.id}>
                <Link
                  href={`/tickets/${ticket.id}`}
                  className="-mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-muted/50"
                >
                  <span className="w-10 text-sm text-muted-foreground tabular-nums">
                    #{ticket.number}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {ticket.subject}
                  </span>
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    {timeAgo(ticket.updated_at)}
                  </span>
                  <StatusBadge status={ticket.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function CustomerDetail() {
  const { id } = useParams<{ id: string }>();
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const router = useRouter();

  const customer = useQuery({
    queryKey: queryKeys.customer(organization.id, id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/customers/{customer_id}", {
          params: { path: { customer_id: id }, header: tenant(organization.id) },
        }),
      ),
  });

  const remove = async () => {
    try {
      await unwrap(
        api.DELETE("/api/v1/customers/{customer_id}", {
          params: { path: { customer_id: id }, header: tenant(organization.id) },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.customers(organization.id) });
      toast.success("Customer deleted");
      router.replace("/customers");
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  const backLink = (
    <Link
      href="/customers"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeftIcon className="size-4" aria-hidden />
      Customers
    </Link>
  );

  if (customer.isPending) {
    return (
      <div className="space-y-4">
        {backLink}
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (customer.isError) {
    const notFound = customer.error instanceof ApiError && customer.error.status === 404;
    return (
      <div className="space-y-4">
        {backLink}
        <Alert variant={notFound ? "default" : "destructive"}>
          <AlertTitle>{notFound ? "Customer not found" : "Couldn't load this customer"}</AlertTitle>
          <AlertDescription>
            {notFound ? "It may have been deleted." : errorMessage(customer.error)}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = customer.data;
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        {backLink}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{data.name}</h1>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {data.email ?? "No email"}
              {data.has_account ? <Badge variant="secondary">Portal access</Badge> : null}
            </div>
          </div>
          {organization.role === "admin" ? (
            <ConfirmAction
              triggerProps={{ variant: "outline" }}
              title={`Delete ${data.name}?`}
              description="Only customers without tickets can be deleted. This can't be undone."
              confirmLabel="Delete"
              onConfirm={remove}
            >
              <Trash2Icon aria-hidden />
              Delete
            </ConfirmAction>
          ) : null}
        </div>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <CustomerTicketList customer={data} />
        <CustomerForm customer={data} />
      </div>
    </div>
  );
}
