import { SparklesIcon } from "lucide-react";
import Link from "next/link";

export function BrandMark({ className = "size-8" }: { className?: string }) {
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground ${className}`}
    >
      <SparklesIcon className="size-1/2" aria-hidden />
    </span>
  );
}

export function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <BrandMark />
      <span className="text-lg">SupportIQ</span>
    </Link>
  );
}
