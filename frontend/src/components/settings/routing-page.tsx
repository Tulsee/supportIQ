"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownIcon, ArrowUpIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { ModeCard } from "@/components/settings/mode-card";
import { PageHeader } from "@/components/page-header";
import { RuleDialog } from "@/components/settings/rule-dialog";
import { RoutingSimulator } from "@/components/settings/routing-simulator";
import { useCategories, useTeams } from "@/components/tickets/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage, tenant, unwrap, type RoutingRule } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROUTING_MODES, STRATEGY_LABELS, describeConditions } from "@/lib/routing";
import { cn } from "@/lib/utils";

function RoutingModeCard() {
  return (
    <ModeCard
      title="Automatic routing"
      description="What happens to new tickets that nobody assigned or sent to a team. A person's choice always wins over routing."
      setting="routing_mode"
      modes={ROUTING_MODES}
    />
  );
}

function useRuleMutations() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const params = { header: tenant(organization.id) };
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.routingRules(organization.id), refetchType: "all" });
  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      unwrap(
        api.PATCH("/api/v1/routing/rules/{rule_id}", {
          params: { ...params, path: { rule_id: id } },
          body: { is_active },
        }),
      ),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reorder = useMutation({
    mutationFn: (rule_ids: string[]) =>
      unwrap(api.PUT("/api/v1/routing/rules/order", { params, body: { rule_ids } })),
    onSuccess: (rules) => queryClient.setQueryData(queryKeys.routingRules(organization.id), rules),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/routing/rules/{rule_id}", { params: { ...params, path: { rule_id: id } } })),
    onSuccess: () => refresh(),
  });
  return { toggle, reorder, remove };
}

function RuleItem({
  rule,
  rules,
  index,
  categoryNames,
}: {
  rule: RoutingRule;
  rules: RoutingRule[];
  index: number;
  categoryNames: Record<string, string>;
}) {
  const { toggle, reorder, remove } = useRuleMutations();
  const move = (to: number) => {
    const ids = rules.map((r) => r.id);
    [ids[index], ids[to]] = [ids[to], ids[index]];
    reorder.mutate(ids);
  };
  const skills = rule.required_skills.map((s) => `${s.skill} ${s.level}+`).join(", ");

  return (
    <li className={cn("flex flex-wrap items-start gap-3 py-3 sm:flex-nowrap", !rule.is_active && "opacity-70")}>
      <span
        className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold tabular-nums"
        aria-hidden
      >
        {index + 1}
      </span>
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{rule.name}</span>
          {rule.is_active ? null : <Badge variant="outline">Off</Badge>}
          {rule.team.is_active ? null : (
            <Badge variant="outline" className="text-amber-700 dark:text-amber-400">
              Team is off: skipped
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          {describeConditions(rule.conditions, categoryNames)} → <span className="text-foreground">{rule.team.name}</span>,{" "}
          {STRATEGY_LABELS[rule.strategy].toLowerCase()}
          {skills ? ` · needs ${skills}` : ""}
        </p>
      </div>
      <div className="flex w-full shrink-0 items-center justify-end gap-1 sm:w-auto">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Move ${rule.name} up`}
          disabled={index === 0 || reorder.isPending}
          onClick={() => move(index - 1)}
        >
          <ArrowUpIcon aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Move ${rule.name} down`}
          disabled={index === rules.length - 1 || reorder.isPending}
          onClick={() => move(index + 1)}
        >
          <ArrowDownIcon aria-hidden />
        </Button>
        <Switch
          checked={rule.is_active}
          onCheckedChange={(checked) => toggle.mutate({ id: rule.id, is_active: checked })}
          aria-label={`${rule.name} on`}
          className="mx-1"
        />
        <RuleDialog
          rule={rule}
          trigger={
            <Button variant="ghost" size="icon-sm" aria-label={`Edit ${rule.name}`}>
              <PencilIcon aria-hidden />
            </Button>
          }
        />
        <ConfirmAction
          triggerProps={{ variant: "ghost", size: "icon-sm", "aria-label": `Delete ${rule.name}` }}
          title={`Delete ${rule.name}?`}
          description="Decisions it already made keep its name. To stop it for a while, turn it off instead."
          confirmLabel="Delete rule"
          onConfirm={() =>
            remove.mutateAsync(rule.id).then(
              () => toast.success(`Deleted ${rule.name}`),
              (error) => {
                toast.error(errorMessage(error));
                throw error;
              },
            )
          }
        >
          <Trash2Icon aria-hidden />
        </ConfirmAction>
      </div>
    </li>
  );
}

function RulesCard() {
  const organization = useActiveOrganization();
  const { teams, isPending: teamsPending } = useTeams();
  const { categories } = useCategories(true);
  const categoryNames = Object.fromEntries(categories.map((c) => [c.key, c.name]));
  const rules = useQuery({
    queryKey: queryKeys.routingRules(organization.id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/routing/rules", { params: { header: tenant(organization.id) } })),
  });
  const noTeams = !teamsPending && teams.length === 0;

  return (
    <Card className="mb-6">
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <CardTitle>
            <h2>Rules</h2>
          </CardTitle>
          <CardDescription>
            Tried from the top. The first rule that matches sends the ticket to its team, and an agent
            is picked who&apos;s online, available, has room and has the skills. No match: the ticket
            stays in the unassigned queue.
          </CardDescription>
        </div>
        <RuleDialog
          trigger={
            <Button disabled={noTeams}>
              <PlusIcon aria-hidden />
              Add rule
            </Button>
          }
        />
      </CardHeader>
      <CardContent>
        {noTeams ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Rules send tickets to teams.{" "}
            <Link href="/settings/teams" className="underline underline-offset-4">
              Add a team
            </Link>{" "}
            first.
          </p>
        ) : rules.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : rules.isError ? (
          <p className="text-sm text-destructive">{errorMessage(rules.error)}</p>
        ) : rules.data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No rules yet.</p>
        ) : (
          <ol className="divide-y" aria-label="Routing rules, in order">
            {rules.data.map((rule, index) => (
              <RuleItem
                key={rule.id}
                rule={rule}
                rules={rules.data}
                index={index}
                categoryNames={categoryNames}
              />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

/** Admins: the routing mode, ordered rules, and a simulator. */
export function RoutingPage() {
  return (
    <>
      <PageHeader
        title="Routing"
        description="Send new tickets to the right team and agent automatically, and see why each one went where it did."
      />
      <RoutingModeCard />
      <RulesCard />
      <RoutingSimulator />
    </>
  );
}
