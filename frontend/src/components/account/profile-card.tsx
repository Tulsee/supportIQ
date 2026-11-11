"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { Loader2Icon } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
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
import { useAuth, useCurrentUser } from "@/lib/auth/auth-provider";
import { formatDate } from "@/lib/roles";

const schema = z.object({ full_name: z.string().trim().min(1, "Enter your name").max(200) });
type Values = z.infer<typeof schema>;

export function ProfileCard() {
  const user = useCurrentUser();
  const { setUser } = useAuth();
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: user.full_name },
  });
  const { errors, isDirty } = form.formState;

  const save = useMutation({
    mutationFn: (values: Values) => unwrap(api.PATCH("/api/v1/users/me", { body: values })),
    onSuccess: (updated) => {
      setUser(updated);
      form.reset({ full_name: updated.full_name });
      toast.success("Profile updated");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card>
      <form onSubmit={form.handleSubmit((values) => save.mutate(values))} noValidate>
        <CardHeader>
          <CardTitle>
            <h2>Profile</h2>
          </CardTitle>
          <CardDescription>
            How you appear to people in your organizations. Member since{" "}
            {formatDate(user.created_at)}.
          </CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            <Field data-invalid={!!errors.full_name}>
              <FieldLabel htmlFor="full_name">Full name</FieldLabel>
              <Input
                id="full_name"
                autoComplete="name"
                aria-invalid={!!errors.full_name}
                {...form.register("full_name")}
              />
              <FieldError errors={[errors.full_name]} />
            </Field>
            <Field data-disabled>
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input id="email" value={user.email} readOnly disabled />
              <FieldDescription>Your sign-in address. It can&apos;t be changed yet.</FieldDescription>
            </Field>
            {user.is_superadmin ? (
              <p className="text-sm text-muted-foreground">
                <Badge>Platform admin</Badge> You can manage every user and organization.
              </p>
            ) : null}
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
