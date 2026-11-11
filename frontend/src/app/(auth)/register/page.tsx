import type { Metadata } from "next";
import { Suspense } from "react";

import { RegisterForm } from "@/components/auth/register-form";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "Create account" };

export default function RegisterPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[30rem] w-full rounded-xl" />}>
      <RegisterForm />
    </Suspense>
  );
}
