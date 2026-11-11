"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2Icon } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { ApiError, api, errorMessage, unwrap } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { session } from "@/lib/auth/session";

// Mirrors the API's password policy (NewPassword) for instant feedback.
const schema = z
  .object({
    current_password: z.string().min(1, "Enter your current password"),
    new_password: z
      .string()
      .min(8, "Use at least 8 characters")
      .max(128, "Use at most 128 characters"),
    confirm_password: z.string(),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    message: "Passwords don't match",
    path: ["confirm_password"],
  });
type Values = z.infer<typeof schema>;

const EMPTY: Values = { current_password: "", new_password: "", confirm_password: "" };

export function PasswordCard() {
  const user = useCurrentUser();
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: EMPTY });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async ({ current_password, new_password }) => {
    try {
      const tokens = await unwrap(
        api.POST("/api/v1/auth/change-password", { body: { current_password, new_password } }),
      );
      // Every session was revoked; this response carries the new one for this tab.
      session.setTokens(tokens.access_token, tokens.expires_in);
      form.reset(EMPTY);
      toast.success("Password changed. Your other devices were signed out.");
    } catch (error) {
      if (error instanceof ApiError && error.code === "invalid_current_password") {
        form.setError("current_password", { message: error.message }, { shouldFocus: true });
      } else if (error instanceof ApiError && error.code === "password_unchanged") {
        form.setError("new_password", { message: error.message }, { shouldFocus: true });
      } else {
        form.setError("root", { message: errorMessage(error) });
      }
    }
  });

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate>
        <CardHeader>
          <CardTitle>
            <h2>Password</h2>
          </CardTitle>
          <CardDescription>
            Changing your password signs you out on every other device.
          </CardDescription>
        </CardHeader>
        <CardContent className="py-6">
          <FieldGroup>
            {/* Lets password managers associate the new password with this account. */}
            <input type="text" autoComplete="username" value={user.email} hidden readOnly />
            <Field data-invalid={!!errors.current_password}>
              <FieldLabel htmlFor="current_password">Current password</FieldLabel>
              <Input
                id="current_password"
                type="password"
                autoComplete="current-password"
                aria-invalid={!!errors.current_password}
                {...form.register("current_password")}
              />
              <FieldError errors={[errors.current_password]} />
            </Field>
            <Field data-invalid={!!errors.new_password}>
              <FieldLabel htmlFor="new_password">New password</FieldLabel>
              <Input
                id="new_password"
                type="password"
                autoComplete="new-password"
                aria-invalid={!!errors.new_password}
                {...form.register("new_password")}
              />
              <FieldDescription>At least 8 characters.</FieldDescription>
              <FieldError errors={[errors.new_password]} />
            </Field>
            <Field data-invalid={!!errors.confirm_password}>
              <FieldLabel htmlFor="confirm_password">Confirm new password</FieldLabel>
              <Input
                id="confirm_password"
                type="password"
                autoComplete="new-password"
                aria-invalid={!!errors.confirm_password}
                {...form.register("confirm_password")}
              />
              <FieldError errors={[errors.confirm_password]} />
            </Field>
            {errors.root ? (
              <Alert variant="destructive">
                <AlertDescription>{errors.root.message}</AlertDescription>
              </Alert>
            ) : null}
          </FieldGroup>
        </CardContent>
        <CardFooter className="justify-end border-t">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
            Change password
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
