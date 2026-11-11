"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2Icon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import { readPendingInvitation } from "@/lib/invitations/pending";
import { postAuthDestination } from "@/lib/navigation";

// Mirrors the API's rules (RegisterRequest) for instant feedback; the API still validates.
const schema = z.object({
  full_name: z.string().trim().min(1, "Enter your name").max(200),
  email: z.email("Enter a valid email address"),
  password: z
    .string()
    .min(8, "Use at least 8 characters")
    .max(128, "Use at most 128 characters"),
});
type RegisterValues = z.infer<typeof schema>;

export function RegisterForm() {
  const { status, register } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const form = useForm<RegisterValues>({
    resolver: zodResolver(schema),
    defaultValues: { full_name: "", email: "", password: "" },
  });
  const { errors, isSubmitting } = form.formState;

  useEffect(() => {
    // Signing up from an invitation: prefill the invited address.
    const invitedEmail = readPendingInvitation()?.email;
    if (invitedEmail) form.setValue("email", invitedEmail);
  }, [form]);

  useEffect(() => {
    if (status === "authenticated") router.replace(postAuthDestination(next));
  }, [status, next, router]);

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await register(values);
      form.reset();
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl"><h1>Create your account</h1></CardTitle>
        <CardDescription>Start with a free workspace for your support team.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate>
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
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="email">Work email</FieldLabel>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                aria-invalid={!!errors.email}
                {...form.register("email")}
              />
              <FieldError errors={[errors.email]} />
            </Field>
            <Field data-invalid={!!errors.password}>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                aria-invalid={!!errors.password}
                {...form.register("password")}
              />
              <FieldDescription>At least 8 characters.</FieldDescription>
              <FieldError errors={[errors.password]} />
            </Field>
            {errors.root ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Create account
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link href={loginHref} className="font-medium text-foreground underline-offset-4 hover:underline">
                Sign in
              </Link>
            </p>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  );
}
