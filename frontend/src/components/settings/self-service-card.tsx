"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TriangleAlertIcon } from "lucide-react";
import Link from "next/link";
import { useId } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage, tenant, unwrap, type Schemas } from "@/lib/api/client";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

type Update = Schemas["OrganizationSettingsUpdate"];
type Model = Schemas["OrganizationSettings"]["self_service_model"];

const MODEL_LABELS: Record<Model, string> = {
  default: "The configured model",
  local: "Always the local model",
};

/** The customer assistant in the portal: on or off, and which model answers it. */
export function SelfServiceCard() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const switchId = useId();
  const modelId = useId();
  const isAdmin = organization.role === "admin";
  const { self_service: mode, self_service_model: model } = organization.settings;

  const publicPage = { limit: 1, offset: 0, visibility: "public" as const };
  const publicDocuments = useQuery({
    queryKey: queryKeys.documents(organization.id, publicPage),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents", {
          params: { query: publicPage, header: tenant(organization.id) },
        }),
      ),
  });
  const publicCount = publicDocuments.data?.total;

  const save = useMutation({
    mutationFn: (settings: Update) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}", {
          params: { path: { org_id: organization.id } },
          body: { settings },
        }),
      ),
    onSuccess: async (updated, settings) => {
      await refreshOrganizations(queryClient);
      toast.success(
        settings.self_service
          ? updated.settings.self_service === "on"
            ? "Customers can now ask the assistant"
            : "The customer assistant is off"
          : `Customers' questions: ${MODEL_LABELS[updated.settings.self_service_model].toLowerCase()}`,
      );
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>
          <h2>Customer assistant</h2>
        </CardTitle>
        <CardDescription>
          Customers ask questions in the portal and get answers from your public documents, with
          sources. Anything the assistant can&apos;t answer reliably goes to your team as a request.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex items-center gap-2">
          <Switch
            id={switchId}
            checked={mode === "on"}
            disabled={!isAdmin || save.isPending}
            onCheckedChange={(checked) => save.mutate({ self_service: checked ? "on" : "off" })}
          />
          <Label htmlFor={switchId} className="font-normal">
            Let customers ask the assistant
          </Label>
        </div>
        {publicCount !== undefined ? (
          publicCount === 0 ? (
            <Alert>
              <TriangleAlertIcon aria-hidden />
              <AlertDescription>
                No document is public yet, so the assistant can&apos;t answer anything. Open a
                document in the{" "}
                <Link href="/knowledge-base" className="underline">
                  knowledge base
                </Link>{" "}
                and turn on “Answers customers”.
              </AlertDescription>
            </Alert>
          ) : (
            <p className="text-sm text-muted-foreground">
              It answers from{" "}
              <Link href="/knowledge-base" className="underline">
                {publicCount} public document{publicCount === 1 ? "" : "s"}
              </Link>
              . Internal documents are never used, even if a customer asks for them.
            </p>
          )
        ) : null}
        <div className="space-y-1.5">
          <Label htmlFor={modelId}>Model for customers&apos; questions</Label>
          <Select
            items={MODEL_LABELS}
            value={model}
            onValueChange={(value) => value && value !== model && save.mutate({ self_service_model: value as Model })}
            disabled={!isAdmin || save.isPending}
          >
            <SelectTrigger id={modelId} className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(MODEL_LABELS) as Model[]).map((value) => (
                <SelectItem key={value} value={value}>
                  {MODEL_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            The local model keeps customers&apos; questions on your own server, even when staff use a
            hosted model.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
