"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon, XIcon } from "lucide-react";
import { useId, useState, type FormEvent, type ReactElement } from "react";
import { toast } from "sonner";

import { SkillsEditor, cleanSkills } from "@/components/settings/skills-editor";
import { useAgents, useCategories, useTeams } from "@/components/tickets/hooks";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type RoutingCondition,
  type RoutingRule,
  type RoutingStrategy,
  type SkillLevel,
} from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import {
  FIELD_LABELS,
  FIELD_OPS,
  OP_LABELS,
  STRATEGIES,
  fromDraft,
  newCondition,
  toDraft,
  type ConditionField,
  type ConditionOp,
  type DraftCondition,
} from "@/lib/routing";
import { PRIORITIES, PRIORITY_LABELS } from "@/lib/tickets";

const STRATEGY_ITEMS = Object.fromEntries(STRATEGIES.map((s) => [s.value, s.label]));

export function useCustomerFields(): string[] {
  const organization = useActiveOrganization();
  const query = useQuery({
    queryKey: queryKeys.customerFields(organization.id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/routing/customer-fields", { params: { header: tenant(organization.id) } })),
  });
  return query.data ?? [];
}

/** Toggle buttons for "is any of" (categories, priorities). */
function Choices({
  options,
  selected,
  onChange,
  label,
}: {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  label: string;
}) {
  if (options.length === 0) {
    return <p className="text-sm text-muted-foreground">No categories yet.</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={label}>
      {options.map((option) => {
        const on = selected.includes(option.value);
        return (
          <Button
            key={option.value}
            type="button"
            size="xs"
            variant={on ? "default" : "outline"}
            aria-pressed={on}
            onClick={() =>
              onChange(on ? selected.filter((v) => v !== option.value) : [...selected, option.value])
            }
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

function ConditionRow({
  condition,
  onChange,
  onRemove,
  position,
  customerFields,
  fieldsListId,
}: {
  condition: DraftCondition;
  onChange: (condition: DraftCondition) => void;
  onRemove: () => void;
  position: string;
  customerFields: string[];
  fieldsListId: string;
}) {
  const { categories } = useCategories(true);
  const ops = FIELD_OPS[condition.field];
  const opItems = Object.fromEntries(ops.map((op) => [op, OP_LABELS[op]]));
  const set = (change: Partial<DraftCondition>) => onChange({ ...condition, ...change });
  const keyInvalid = condition.field === "customer" && condition.key !== "" && !/^[a-z0-9_]{1,50}$/.test(condition.key);

  return (
    <li className="space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={FIELD_LABELS}
          value={condition.field}
          onValueChange={(value) => {
            if (!value || value === condition.field) return;
            const field = value as ConditionField;
            onChange({ ...newCondition(field), id: condition.id });
          }}
        >
          <SelectTrigger aria-label={`${position}: what to test`} className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(FIELD_LABELS) as ConditionField[]).map((field) => (
              <SelectItem key={field} value={field}>
                {FIELD_LABELS[field]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {condition.field === "customer" ? (
          <Input
            aria-label={`${position}: customer field`}
            aria-invalid={keyInvalid}
            list={fieldsListId}
            placeholder="e.g. plan"
            value={condition.key}
            onChange={(event) => set({ key: event.target.value.toLowerCase().replace(/\s+/g, "_") })}
            className="w-36 font-mono"
            maxLength={50}
          />
        ) : null}
        {ops.length > 1 ? (
          <Select items={opItems} value={condition.op} onValueChange={(op) => op && set({ op: op as ConditionOp })}>
            <SelectTrigger aria-label={`${position}: how to compare`} className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ops.map((op) => (
                <SelectItem key={op} value={op}>
                  {OP_LABELS[op]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <span className="text-sm text-muted-foreground">{OP_LABELS[condition.op]}</span>
        )}
        {(condition.field === "subject" ||
          condition.field === "customer" ||
          condition.field === "language") &&
        condition.op !== "is_set" &&
        condition.op !== "not_set" ? (
          <Input
            aria-label={`${position}: value`}
            placeholder={
              condition.field === "language"
                ? "ne, hi (language codes)"
                : condition.op === "in"
                  ? "gold, platinum"
                  : condition.field === "subject"
                    ? "e.g. refund"
                    : "value"
            }
            value={condition.text}
            onChange={(event) => set({ text: event.target.value })}
            className="min-w-40 flex-1"
            maxLength={200}
          />
        ) : null}
        <Button type="button" variant="ghost" size="icon-sm" className="ml-auto" aria-label={`Remove ${position}`} onClick={onRemove}>
          <XIcon aria-hidden />
        </Button>
      </div>
      {condition.op === "in" && condition.field === "category" ? (
        <Choices
          label={`${position}: categories`}
          options={categories.map((c) => ({ value: c.key, label: c.is_active ? c.name : `${c.name} (off)` }))}
          selected={condition.values}
          onChange={(values) => set({ values })}
        />
      ) : null}
      {condition.op === "in" && condition.field === "priority" ? (
        <Choices
          label={`${position}: priorities`}
          options={PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABELS[p] }))}
          selected={condition.values}
          onChange={(values) => set({ values })}
        />
      ) : null}
      {condition.field === "customer" && customerFields.length === 0 && condition.key === "" ? (
        <p className="text-xs text-muted-foreground">
          Customer fields come from the details saved on customers (for example <code>plan</code>).
        </p>
      ) : null}
    </li>
  );
}

function ConditionList({
  title,
  hint,
  conditions,
  onChange,
  customerFields,
  fieldsListId,
}: {
  title: string;
  hint: string;
  conditions: DraftCondition[];
  onChange: (conditions: DraftCondition[]) => void;
  customerFields: string[];
  fieldsListId: string;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{title}</legend>
      <p className="text-xs text-muted-foreground">{hint}</p>
      {conditions.length ? (
        <ul className="space-y-2">
          {conditions.map((condition, index) => (
            <ConditionRow
              key={condition.id}
              position={`${title} ${index + 1}`}
              condition={condition}
              customerFields={customerFields}
              fieldsListId={fieldsListId}
              onChange={(next) => onChange(conditions.map((c) => (c.id === condition.id ? next : c)))}
              onRemove={() => onChange(conditions.filter((c) => c.id !== condition.id))}
            />
          ))}
        </ul>
      ) : null}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...conditions, newCondition()])}>
        <PlusIcon aria-hidden />
        Add condition
      </Button>
    </fieldset>
  );
}

type RuleBody = {
  name: string;
  team_id: string;
  strategy: RoutingStrategy;
  required_skills: SkillLevel[];
  conditions: { all: RoutingCondition[]; any: RoutingCondition[] };
  is_active: boolean;
};

/** Add a routing rule, or edit `rule`. */
export function RuleDialog({ rule, trigger }: { rule?: RoutingRule; trigger: ReactElement }) {
  const uid = useId();
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const { teams } = useTeams();
  const { agents } = useAgents();
  const customerFields = useCustomerFields();
  const knownSkills = [...new Set(agents.flatMap((a) => a.skills.map((s) => s.skill)))].sort();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [teamId, setTeamId] = useState("");
  const [strategy, setStrategy] = useState<RoutingStrategy>("least_loaded");
  const [skills, setSkills] = useState<SkillLevel[]>([]);
  const [all, setAll] = useState<DraftCondition[]>([]);
  const [any, setAny] = useState<DraftCondition[]>([]);

  function reset() {
    setName(rule?.name ?? "");
    setTeamId(rule?.team.id ?? "");
    setStrategy(rule?.strategy ?? "least_loaded");
    setSkills(rule?.required_skills ?? []);
    setAll((rule?.conditions.all ?? []).map(toDraft));
    setAny((rule?.conditions.any ?? []).map(toDraft));
  }

  const save = useMutation({
    mutationFn: (body: RuleBody) => {
      const params = { header: tenant(organization.id) };
      return rule
        ? unwrap(
            api.PATCH("/api/v1/routing/rules/{rule_id}", {
              params: { ...params, path: { rule_id: rule.id } },
              body,
            }),
          )
        : unwrap(api.POST("/api/v1/routing/rules", { params, body }));
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.routingRules(organization.id),
        refetchType: "all",
      });
      toast.success(rule ? `Saved ${saved.name}` : `Added ${saved.name}`);
      setOpen(false);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const teamItems: Record<string, string> = Object.fromEntries(teams.map((t) => [t.id, t.name]));
  if (rule && !(rule.team.id in teamItems)) teamItems[rule.team.id] = `${rule.team.name} (off)`;
  const allConditions = all.map(fromDraft);
  const anyConditions = any.map(fromDraft);
  const conditionsComplete = [...allConditions, ...anyConditions].every((c) => c !== null);
  const cleaned = cleanSkills(skills);
  const valid = name.trim().length >= 2 && teamId !== "" && conditionsComplete && cleaned !== null;
  const strategyHelp = STRATEGIES.find((s) => s.value === strategy)?.description;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!valid) return;
    save.mutate({
      name: name.trim(),
      team_id: teamId,
      strategy,
      required_skills: strategy === "manual" ? [] : cleaned!,
      conditions: { all: allConditions as RoutingCondition[], any: anyConditions as RoutingCondition[] },
      is_active: rule?.is_active ?? true,
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) reset();
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={submit} noValidate>
          <DialogHeader>
            <DialogTitle>{rule ? `Edit ${rule.name}` : "Add a routing rule"}</DialogTitle>
            <DialogDescription>
              Rules are tried from the top; the first one that matches a new ticket decides where it goes.
            </DialogDescription>
          </DialogHeader>
          <datalist id={`${uid}-fields`}>
            {customerFields.map((field) => (
              <option key={field} value={field} />
            ))}
          </datalist>
          <FieldGroup className="my-4 gap-6">
            <Field>
              <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
              <Input
                id={`${uid}-name`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Billing questions"
                maxLength={100}
              />
            </Field>
            <ConditionList
              title="All of these"
              hint="Every condition here must hold. Leave both lists empty to match every ticket."
              conditions={all}
              onChange={setAll}
              customerFields={customerFields}
              fieldsListId={`${uid}-fields`}
            />
            <ConditionList
              title="And one of these"
              hint="At least one condition here must hold too (if there are any)."
              conditions={any}
              onChange={setAny}
              customerFields={customerFields}
              fieldsListId={`${uid}-fields`}
            />
            {!conditionsComplete ? (
              <p className="text-sm text-destructive" role="alert">
                Finish or remove the incomplete conditions (pick values, fill in text and field names).
              </p>
            ) : null}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field>
                <FieldLabel htmlFor={`${uid}-team`}>Send to team</FieldLabel>
                <Select items={teamItems} value={teamId || null} onValueChange={(value) => value && setTeamId(value)}>
                  <SelectTrigger id={`${uid}-team`} className="w-full">
                    <SelectValue placeholder="Choose a team" />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(teamItems).map(([id, teamName]) => (
                      <SelectItem key={id} value={id}>
                        {teamName}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field>
                <FieldLabel htmlFor={`${uid}-strategy`}>Pick an agent by</FieldLabel>
                <Select
                  items={STRATEGY_ITEMS}
                  value={strategy}
                  onValueChange={(value) => value && setStrategy(value as RoutingStrategy)}
                >
                  <SelectTrigger id={`${uid}-strategy`} className="w-full" aria-describedby={`${uid}-strategy-help`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {STRATEGIES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FieldDescription id={`${uid}-strategy-help`}>{strategyHelp}</FieldDescription>
              </Field>
            </div>
            {strategy !== "manual" ? (
              <Field>
                <FieldLabel>Required skills</FieldLabel>
                <SkillsEditor
                  value={skills}
                  onChange={setSkills}
                  suggestions={knownSkills}
                  label="Required skills"
                  addLabel="Require a skill"
                />
                <FieldDescription>Only agents with every one, at least at that level, are picked.</FieldDescription>
                {cleaned === null ? (
                  <p className="text-sm text-destructive" role="alert">
                    Use lower-case words joined by - or _ (like <code>refunds</code>), each skill once.
                  </p>
                ) : null}
              </Field>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={!valid || save.isPending}>
              {save.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              {rule ? "Save rule" : "Add rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
