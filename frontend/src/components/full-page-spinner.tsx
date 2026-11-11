import { Loader2Icon } from "lucide-react";

export function FullPageSpinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex min-h-svh flex-1 items-center justify-center" role="status">
      <Loader2Icon className="size-6 animate-spin text-muted-foreground" aria-hidden />
      <span className="sr-only">{label}</span>
    </div>
  );
}
