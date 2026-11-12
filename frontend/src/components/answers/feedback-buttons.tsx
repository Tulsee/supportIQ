"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, errorMessage, tenant, unwrap } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

type Rating = "helpful" | "not_helpful";

/** Rate an AI run as helpful or not (your rating replaces any earlier one). */
export function FeedbackButtons({ runId, className }: { runId: string; className?: string }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const key = queryKeys.aiFeedback(organization.id, runId);
  const feedback = useQuery({
    queryKey: key,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/ai/runs/{run_id}/feedback", {
          params: { path: { run_id: runId }, header: tenant(organization.id) },
        }),
      ),
    staleTime: 60_000,
  });
  const rate = useMutation({
    mutationFn: (rating: Rating) =>
      unwrap(
        api.PUT("/api/v1/ai/runs/{run_id}/feedback", {
          params: { path: { run_id: runId }, header: tenant(organization.id) },
          body: { rating },
        }),
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      void queryClient.invalidateQueries({
        queryKey: ["organizations", organization.id, "analytics"],
        refetchType: "all",
      });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const mine = rate.isPending ? rate.variables : feedback.data?.mine;

  return (
    <div className={cn("inline-flex items-center gap-0.5", className)} role="group" aria-label="Rate this answer">
      {(
        [
          ["helpful", ThumbsUpIcon, "Helpful"],
          ["not_helpful", ThumbsDownIcon, "Not helpful"],
        ] as const
      ).map(([rating, Icon, label]) => (
        <Button
          key={rating}
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          aria-pressed={mine === rating}
          title={label}
          disabled={rate.isPending}
          onClick={() => rate.mutate(rating)}
          className={cn(mine === rating && "bg-muted text-foreground")}
        >
          <Icon className={cn("size-3.5", mine === rating && "fill-current")} aria-hidden />
        </Button>
      ))}
    </div>
  );
}
