import type { ReactNode } from "react";

import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-svh flex-1 flex-col items-center justify-center gap-6 bg-muted/40 p-6">
      <Brand />
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
