"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { FullPageSpinner } from "@/components/full-page-spinner";
import { useAuth } from "@/lib/auth/auth-provider";

/** Entry point: send the visitor to their workspace or to sign in. */
export default function HomePage() {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status !== "loading") router.replace(status === "authenticated" ? "/dashboard" : "/login");
  }, [status, router]);

  return <FullPageSpinner />;
}
