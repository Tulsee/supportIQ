"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PencilIcon, PlusIcon } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { ModeCard } from "@/components/settings/mode-card";
import { useCategories } from "@/components/tickets/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage, tenant, unwrap, type Category } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { TRIAGE_MODES } from "@/lib/triage";

type CategoryChange = { name?: string; description?: string; is_active?: boolean };

function useCategoryMutations() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const params = { header: tenant(organization.id) };
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.categories(organization.id), refetchType: "all" });

  const create = useMutation({
    mutationFn: (body: { name: string; key?: string; description: string }) =>
      unwrap(api.POST("/api/v1/categories", { params, body })),
    onSuccess: (category) => {
      toast.success(`Added ${category.name}`);
      return refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: CategoryChange }) =>
      unwrap(
        api.PATCH("/api/v1/categories/{category_id}", {
          params: { ...params, path: { category_id: id } },
          body,
        }),
      ),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  return { create, update };
}

function AddCategory() {
  const uid = useId();
  const { create } = useCategoryMutations();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { name: name.trim(), key: key.trim() || undefined, description: description.trim() },
      {
        onSuccess: () => {
          setName("");
          setKey("");
          setDescription("");
        },
      },
    );
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>
          <h2>Add a category</h2>
        </CardTitle>
        <CardDescription>
          What tickets are about, such as Billing or Wi-Fi setup. Staff set it on tickets; later,
          routing rules use it to send tickets to the right team.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} noValidate>
          <FieldGroup className="gap-4 sm:grid sm:grid-cols-[1fr_12rem] sm:items-start">
            <Field>
              <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={100}
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${uid}-key`}>Key (optional)</FieldLabel>
              <Input
                id={`${uid}-key`}
                value={key}
                onChange={(event) => setKey(event.target.value.toLowerCase())}
                placeholder="from the name"
                maxLength={50}
                className="font-mono"
                aria-describedby={`${uid}-key-help`}
              />
              <FieldDescription id={`${uid}-key-help`}>Can&apos;t be changed later.</FieldDescription>
            </Field>
            <Field className="sm:col-span-2">
              <FieldLabel htmlFor={`${uid}-description`}>Description (optional)</FieldLabel>
              <Input
                id={`${uid}-description`}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={300}
              />
            </Field>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={name.trim().length < 2 || create.isPending}>
                {create.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <PlusIcon aria-hidden />}
                Add category
              </Button>
            </div>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  );
}

function EditCategory({ category }: { category: Category }) {
  const uid = useId();
  const { update } = useCategoryMutations();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(category.name);
  const [description, setDescription] = useState(category.description);

  function submit(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { id: category.id, body: { name: name.trim(), description: description.trim() } },
      {
        onSuccess: () => {
          toast.success("Category updated");
          setOpen(false);
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setName(category.name);
          setDescription(category.description);
        }
      }}
    >
      <DialogTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Edit ${category.name}`} />}>
        <PencilIcon aria-hidden />
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} noValidate>
          <DialogHeader>
            <DialogTitle>Edit {category.name}</DialogTitle>
            <DialogDescription>
              The key <code className="font-mono">{category.key}</code> stays the same.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup className="my-4 gap-4">
            <Field>
              <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
              <Input id={`${uid}-name`} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${uid}-description`}>Description</FieldLabel>
              <Input
                id={`${uid}-description`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={300}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={name.trim().length < 2 || update.isPending}>
              {update.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Admins: the organization's ticket categories. */
export function CategoriesPage() {
  const { categories, isPending } = useCategories(true);
  const { update } = useCategoryMutations();

  return (
    <>
      <PageHeader
        title="Ticket categories"
        description="Sort tickets by topic. Categories are turned off rather than deleted, so older tickets keep theirs."
      />
      <ModeCard
        title="AI triage"
        description="Suggest each new ticket's category, priority and language from what the customer wrote and from similar tickets your team already sorted. When it isn't sure, it leaves the category for a person."
        setting="triage_mode"
        modes={TRIAGE_MODES}
      />
      <AddCategory />
      <Card>
        <CardContent>
          {isPending ? (
            <Skeleton className="h-32 w-full" />
          ) : categories.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">No categories yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead className="hidden sm:table-cell">Key</TableHead>
                  <TableHead className="w-24">In use</TableHead>
                  <TableHead className="w-12">
                    <span className="sr-only">Edit</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {categories.map((category) => (
                  <TableRow key={category.id}>
                    <TableCell className="whitespace-normal">
                      <div className="flex flex-wrap items-center gap-2 font-medium">
                        {category.name}
                        {category.is_active ? null : <Badge variant="outline">Off</Badge>}
                      </div>
                      {category.description ? (
                        <div className="text-xs text-muted-foreground">{category.description}</div>
                      ) : null}
                    </TableCell>
                    <TableCell className="hidden font-mono text-xs text-muted-foreground sm:table-cell">
                      {category.key}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={category.is_active}
                        onCheckedChange={(checked) =>
                          update.mutate({ id: category.id, body: { is_active: checked } })
                        }
                        aria-label={`${category.name} in use`}
                      />
                    </TableCell>
                    <TableCell>
                      <EditCategory category={category} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
