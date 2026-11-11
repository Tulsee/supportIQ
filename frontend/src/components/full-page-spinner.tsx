import { Loader2Icon } from "lucide-react";

import { cn } from "@/lib/utils";

export function FullPageSpinner({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn("flex min-h-svh flex-1 items-center justify-center", className)}
      role="status"
    >
      <Loader2Icon className="size-6 animate-spin text-muted-foreground" aria-hidden />
      <span className="sr-only">{label}</span>
    </div>
  );
}
