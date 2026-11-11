"use client";

import { useQuery } from "@tanstack/react-query";
import { ServerOffIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { api, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";

const WORKER_COMMAND =
  "uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo";

/** Whether a background worker is running. Only asked while something is queued. */
export function useWorkerOffline(enabled: boolean): boolean {
  const organization = useActiveOrganization();
  const status = useQuery({
    queryKey: ["organizations", organization.id, "documents", "workers"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents/workers", { params: { header: tenant(organization.id) } }),
      ),
    enabled,
    staleTime: 0,
    refetchInterval: enabled ? 10_000 : false,
  });
  return enabled && status.data?.status === "offline";
}

export function NoWorkerAlert() {
  return (
    <Alert variant="destructive">
      <ServerOffIcon aria-hidden />
      <AlertTitle>No indexing worker is running</AlertTitle>
      <AlertDescription className="space-y-2">
        <p>
          Uploaded documents wait in the queue and are indexed as soon as a worker starts. Nothing
          is lost in the meantime.
        </p>
        {process.env.NODE_ENV === "development" ? (
          <p>
            Start one from <code className="font-mono">backend/</code>:{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">
              {WORKER_COMMAND}
            </code>
          </p>
        ) : (
          <p>Ask whoever runs SupportIQ to start the background worker.</p>
        )}
      </AlertDescription>
    </Alert>
  );
}
