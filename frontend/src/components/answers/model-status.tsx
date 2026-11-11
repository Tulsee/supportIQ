"use client";

import { useQuery } from "@tanstack/react-query";
import { ServerOffIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { api, tenant, unwrap, type ModelStatus } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

/** The answer model and whether it can answer now (it is never run by this check). */
export function useModelStatus() {
  const organization = useActiveOrganization();
  return useQuery({
    queryKey: queryKeys.aiModel(organization.id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/ai/model", { params: { header: tenant(organization.id) } })),
    staleTime: 15_000,
    // While it's down, notice when it comes back.
    refetchInterval: (query) => (query.state.data && query.state.data.status !== "ready" ? 10_000 : false),
  });
}

function Command({ children }: { children: string }) {
  return (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">{children}</code>
  );
}

const TITLES: Record<Exclude<ModelStatus["status"], "ready">, string> = {
  unreachable: "The answer model isn't running",
  not_installed: "The answer model isn't installed",
  not_configured: "The answer model isn't configured",
};

function DevelopmentHint({ status }: { status: ModelStatus }) {
  if (status.provider === "ollama") {
    return status.status === "not_installed" ? (
      <p>
        Download it with <Command>{`ollama pull ${status.model}`}</Command> (a few GB, once).
      </p>
    ) : (
      <p>
        Start Ollama (the desktop app, or <Command>ollama serve</Command>). Questions fail until
        it is running.
      </p>
    );
  }
  return (
    <p>
      Set <Command>ANTHROPIC_API_KEY</Command> in <Command>.env</Command> and restart the API.
    </p>
  );
}

export function ModelStatusAlert({ status }: { status: ModelStatus }) {
  if (status.status === "ready") return null;
  return (
    <Alert variant="destructive">
      <ServerOffIcon aria-hidden />
      <AlertTitle>{TITLES[status.status]}</AlertTitle>
      <AlertDescription className="space-y-2">
        {process.env.NODE_ENV === "development" ? (
          <DevelopmentHint status={status} />
        ) : (
          <p>Ask whoever runs SupportIQ to start the answer model.</p>
        )}
      </AlertDescription>
    </Alert>
  );
}
