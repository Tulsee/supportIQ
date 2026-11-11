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
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import { postAuthDestination } from "@/lib/navigation";

const schema = z.object({
  email: z.email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
type LoginValues = z.infer<typeof schema>;

export function LoginForm() {
  const { status, login } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const form = useForm<LoginValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });
  const { errors, isSubmitting } = form.formState;

  // Already signed in (e.g. opened /login in a new tab): continue.
  useEffect(() => {
    if (status === "authenticated") router.replace(postAuthDestination(next));
  }, [status, next, router]);

  const onSubmit = form.handleSubmit(async ({ email, password }) => {
    try {
      await login(email, password);
      // Clear the password: with route state preservation the page could reappear.
      form.reset();
    } catch (error) {
      form.setError("root", { message: errorMessage(error) });
    }
  });

  const registerHref = next ? `/register?next=${encodeURIComponent(next)}` : "/register";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl"><h1>Sign in</h1></CardTitle>
        <CardDescription>Welcome back. Sign in to your workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate>
          <FieldGroup>
            <Field data-invalid={!!errors.email}>
              <FieldLabel htmlFor="email">Email</FieldLabel>
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
                autoComplete="current-password"
                aria-invalid={!!errors.password}
                {...form.register("password")}
              />
              <FieldError errors={[errors.password]} />
            </Field>
            {errors.root ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
            <Button type="submit" size="lg" disabled={isSubmitting}>
              {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Sign in
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              New to SupportIQ?{" "}
              <Link href={registerHref} className="font-medium text-foreground underline-offset-4 hover:underline">
                Create an account
              </Link>
            </p>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  );
}
