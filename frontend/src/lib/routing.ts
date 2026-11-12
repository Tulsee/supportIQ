import type {
  AgentPresence,
  RoutingCondition,
  RoutingMode,
  RoutingOutcome,
  RoutingStrategy,
} from "@/lib/api/client";
import { PRIORITY_LABELS } from "@/lib/tickets";

export const STRATEGIES: { value: RoutingStrategy; label: string; description: string }[] = [
  {
    value: "least_loaded",
    label: "Least loaded",
    description: "The agent using the smallest share of their capacity.",
  },
  {
    value: "round_robin",
    label: "Round robin",
    description: "Takes turns: whoever was assigned automatically least recently.",
  },
  {
    value: "skill_match",
    label: "Best skill match",
    description: "The highest total level in the required skills, then the least loaded.",
  },
  {
    value: "manual",
    label: "Team only",
    description: "Only sets the team; someone in it picks the ticket up.",
  },
];
export const STRATEGY_LABELS = Object.fromEntries(STRATEGIES.map((s) => [s.value, s.label])) as Record<
  RoutingStrategy,
  string
>;

export const ROUTING_MODES: { value: RoutingMode; label: string; description: string }[] = [
  {
    value: "off",
    label: "Off",
    description: "New tickets wait in the queue. Staff can still reroute a ticket by hand.",
  },
  {
    value: "shadow",
    label: "Shadow",
    description:
      "Every new ticket gets a recorded decision, but nothing is assigned. Compare it with what people do before turning routing on.",
  },
  {
    value: "on",
    label: "On",
    description: "New tickets go to a team and an agent as soon as they're opened.",
  },
];

// Text always says the outcome; color only reinforces it.
export const OUTCOMES: Record<RoutingOutcome, { label: string; className: string }> = {
  assigned: { label: "Assigned", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  team_only: { label: "Sent to team", className: "bg-sky-500/15 text-sky-700 dark:text-sky-400" },
  no_agent: { label: "Nobody available", className: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  no_rule: { label: "No rule matched", className: "bg-muted text-muted-foreground" },
};

export const PRESENCE: Record<AgentPresence, { label: string; dot: string }> = {
  available: { label: "Available", dot: "bg-emerald-500" },
  busy: { label: "Busy", dot: "bg-amber-500" },
  away: { label: "Away", dot: "bg-muted-foreground" },
  offline: { label: "Offline", dot: "border border-muted-foreground bg-transparent" },
};

// --- Conditions, as the rule editor shows them ---------------------------------------------

/** What a condition tests: the ticket's category, priority or subject, or a customer field. */
export type ConditionField = "category" | "priority" | "subject" | "customer";

/** The choices offered per field. `eq` on category or priority is edited as "is any of". */
export type ConditionOp = "in" | "contains" | "eq" | "is_set" | "not_set";

export const FIELD_LABELS: Record<ConditionField, string> = {
  category: "Category",
  priority: "Priority",
  subject: "Subject",
  customer: "Customer field",
};

export const FIELD_OPS: Record<ConditionField, ConditionOp[]> = {
  category: ["in", "not_set", "is_set"],
  priority: ["in"],
  subject: ["contains"],
  customer: ["eq", "in", "contains", "is_set", "not_set"],
};

export const OP_LABELS: Record<ConditionOp, string> = {
  in: "is any of",
  eq: "is",
  contains: "contains",
  is_set: "is set",
  not_set: "is not set",
};

/** A condition while it's being edited. */
export type DraftCondition = {
  id: string;
  field: ConditionField;
  key: string; // customer field name
  op: ConditionOp;
  values: string[]; // "is any of"
  text: string; // "is" / "contains"
};

let nextId = 0;
export function newCondition(field: ConditionField = "category"): DraftCondition {
  nextId += 1;
  return { id: `c${nextId}`, field, key: "", op: FIELD_OPS[field][0], values: [], text: "" };
}

export function toDraft(condition: RoutingCondition): DraftCondition {
  const draft = newCondition();
  const customer = condition.field.startsWith("customer.");
  draft.field = customer ? "customer" : (condition.field as ConditionField);
  draft.key = customer ? condition.field.slice("customer.".length) : "";
  const value = condition.value;
  if (condition.operator === "is_set") {
    draft.op = value ? "is_set" : "not_set";
  } else if (condition.operator === "in") {
    draft.op = "in";
    draft.values = value as string[];
  } else if (condition.operator === "eq" && !customer) {
    draft.op = "in"; // one category or priority: the same as "is any of" it
    draft.values = [value as string];
  } else {
    draft.op = condition.operator;
    draft.text = String(value);
  }
  return draft;
}

/** The API's form, or null while the condition isn't complete. */
export function fromDraft(draft: DraftCondition): RoutingCondition | null {
  const field = draft.field === "customer" ? `customer.${draft.key.trim()}` : draft.field;
  if (draft.field === "customer" && !/^[a-z0-9_]{1,50}$/.test(draft.key.trim())) return null;
  switch (draft.op) {
    case "is_set":
    case "not_set":
      return { field, operator: "is_set", value: draft.op === "is_set" };
    case "in": {
      const values =
        draft.field === "customer"
          ? draft.text
              .split(",")
              .map((v) => v.trim())
              .filter(Boolean)
          : draft.values;
      return values.length ? { field, operator: "in", value: values } : null;
    }
    default:
      return draft.text.trim() ? { field, operator: draft.op, value: draft.text.trim() } : null;
  }
}

/** One condition in words, as the rule list shows it. */
export function describeCondition(
  condition: RoutingCondition,
  categoryNames: Record<string, string>,
): string {
  const customer = condition.field.startsWith("customer.");
  const name = customer
    ? `customer ${condition.field.slice("customer.".length).replaceAll("_", " ")}`
    : FIELD_LABELS[condition.field as ConditionField].toLowerCase();
  const show = (value: string) =>
    condition.field === "category"
      ? (categoryNames[value] ?? value)
      : condition.field === "priority"
        ? (PRIORITY_LABELS[value as keyof typeof PRIORITY_LABELS] ?? value)
        : value;
  const value = condition.value;
  switch (condition.operator) {
    case "is_set":
      return `${name} ${value ? "is set" : "is not set"}`;
    case "in": {
      const values = (value as string[]).map(show);
      return values.length === 1 ? `${name} is ${values[0]}` : `${name} is ${values.join(" or ")}`;
    }
    case "eq":
      return `${name} is ${show(String(value))}`;
    case "contains":
      return `${name} contains “${value}”`;
  }
}

export function describeConditions(
  conditions: { all?: RoutingCondition[]; any?: RoutingCondition[] },
  categoryNames: Record<string, string>,
): string {
  const all = (conditions.all ?? []).map((c) => describeCondition(c, categoryNames));
  const any = (conditions.any ?? []).map((c) => describeCondition(c, categoryNames));
  const parts = [...all];
  if (any.length === 1) parts.push(any[0]);
  else if (any.length > 1) parts.push(`(${any.join(" or ")})`);
  return parts.length ? `When ${parts.join(" and ")}` : "Every ticket";
}
