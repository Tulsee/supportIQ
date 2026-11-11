import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    // LoginForm reads ?next= (useSearchParams), so it renders behind a boundary.
    <Suspense fallback={<Skeleton className="h-96 w-full rounded-xl" />}>
      <LoginForm />
    </Suspense>
  );
}
