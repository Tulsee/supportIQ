"use client";

import { useMutation } from "@tanstack/react-query";
import { FlaskConicalIcon, Loader2Icon, PlusIcon, XIcon } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { DecisionDetails, OutcomeBadge } from "@/components/routing/decision-details";
import { useCustomerFields } from "@/components/settings/rule-dialog";
import { useCategories } from "@/components/tickets/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type RoutingSimulation,
  type TicketPriority,
} from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { STRATEGY_LABELS } from "@/lib/routing";
import { PRIORITIES, PRIORITY_LABELS } from "@/lib/tickets";

const NO_CATEGORY = "none";
type Mode = "example" | "ticket";
type Pair = { key: string; value: string };

type Request =
  | { kind: "ticket"; number: string }
  | {
      kind: "example";
      category: string | null;
      priority: TicketPriority;
      subject: string;
      customer: Record<string, string>;
    };

/** Admins: what routing would do with a ticket right now. Changes nothing. */
export function RoutingSimulator() {
  const uid = useId();
  const organization = useActiveOrganization();
  const header = tenant(organization.id);
  const { categories } = useCategories(true);
  const customerFields = useCustomerFields();
  const [mode, setMode] = useState<Mode>("example");
  const [number, setNumber] = useState("");
  const [category, setCategory] = useState(NO_CATEGORY);
  const [priority, setPriority] = useState<TicketPriority>("normal");
  const [subject, setSubject] = useState("");
  const [fields, setFields] = useState<Pair[]>([]);

  const simulate = useMutation({
    mutationFn: async (request: Request): Promise<RoutingSimulation> => {
      if (request.kind === "ticket") {
        const found = await unwrap(
          api.GET("/api/v1/tickets", {
            params: { query: { search: `#${request.number}`, limit: 5 }, header },
          }),
        );
        const ticket = found.items.find((t) => String(t.number) === request.number);
        if (!ticket) throw new Error(`There's no ticket #${request.number}.`);
        return unwrap(api.POST("/api/v1/routing/simulate", { params: { header }, body: { ticket_id: ticket.id } }));
      }
      const body = {
        category: request.category,
        priority: request.priority,
        subject: request.subject,
        customer: request.customer,
      };
      return unwrap(api.POST("/api/v1/routing/simulate", { params: { header }, body }));
    },
  });

  const categoryItems: Record<string, string> = {
    [NO_CATEGORY]: "No category",
    ...Object.fromEntries(categories.map((c) => [c.key, c.name])),
  };
  const ticketNumber = number.trim().replace(/^#/, "");
  const ready = mode === "ticket" ? /^\d+$/.test(ticketNumber) : true;

  function run(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    if (mode === "ticket") {
      simulate.mutate({ kind: "ticket", number: ticketNumber });
      return;
    }
    const customer = Object.fromEntries(
      fields.filter((f) => f.key.trim()).map((f) => [f.key.trim(), f.value.trim()]),
    );
    simulate.mutate({
      kind: "example",
      category: category === NO_CATEGORY ? null : category,
      priority,
      subject: subject.trim(),
      customer,
    });
  }

  const result = simulate.data;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Try it</h2>
        </CardTitle>
        <CardDescription>
          See where a ticket would go right now, with the agents&apos; current status and workload.
          Nothing is assigned or recorded.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList>
            <TabsTrigger value="example">Example ticket</TabsTrigger>
            <TabsTrigger value="ticket">Existing ticket</TabsTrigger>
          </TabsList>
        </Tabs>
        <form onSubmit={run} noValidate className="space-y-4">
          {mode === "ticket" ? (
            <Field className="max-w-48">
              <FieldLabel htmlFor={`${uid}-number`}>Ticket number</FieldLabel>
              <Input
                id={`${uid}-number`}
                inputMode="numeric"
                placeholder="#42"
                value={number}
                onChange={(event) => setNumber(event.target.value)}
              />
            </Field>
          ) : (
            <FieldGroup className="gap-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_10rem_10rem]">
                <Field>
                  <FieldLabel htmlFor={`${uid}-subject`}>Subject</FieldLabel>
                  <Input
                    id={`${uid}-subject`}
                    value={subject}
                    placeholder="e.g. Refund for a double charge"
                    onChange={(event) => setSubject(event.target.value)}
                    maxLength={300}
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor={`${uid}-category`}>Category</FieldLabel>
                  <Select items={categoryItems} value={category} onValueChange={(v) => v && setCategory(v)}>
                    <SelectTrigger id={`${uid}-category`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(categoryItems).map(([key, name]) => (
                        <SelectItem key={key} value={key}>
                          {name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field>
                  <FieldLabel htmlFor={`${uid}-priority`}>Priority</FieldLabel>
                  <Select
                    items={PRIORITY_LABELS}
                    value={priority}
                    onValueChange={(v) => v && setPriority(v as TicketPriority)}
                  >
                    <SelectTrigger id={`${uid}-priority`} className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PRIORITIES.map((p) => (
                        <SelectItem key={p} value={p}>
                          {PRIORITY_LABELS[p]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">Customer fields</legend>
                <datalist id={`${uid}-fields`}>
                  {customerFields.map((field) => (
                    <option key={field} value={field} />
                  ))}
                </datalist>
                {fields.map((pair, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      aria-label={`Customer field ${index + 1}`}
                      list={`${uid}-fields`}
                      placeholder="plan"
                      className="w-40 font-mono"
                      value={pair.key}
                      onChange={(event) =>
                        setFields(fields.map((f, i) => (i === index ? { ...f, key: event.target.value } : f)))
                      }
                    />
                    <Input
                      aria-label={`Value of customer field ${index + 1}`}
                      placeholder="gold"
                      value={pair.value}
                      onChange={(event) =>
                        setFields(fields.map((f, i) => (i === index ? { ...f, value: event.target.value } : f)))
                      }
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove customer field ${index + 1}`}
                      onClick={() => setFields(fields.filter((_, i) => i !== index))}
                    >
                      <XIcon aria-hidden />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setFields([...fields, { key: "", value: "" }])}
                >
                  <PlusIcon aria-hidden />
                  Add customer field
                </Button>
              </fieldset>
            </FieldGroup>
          )}
          <Button type="submit" disabled={!ready || simulate.isPending}>
            {simulate.isPending ? (
              <Loader2Icon className="animate-spin" aria-hidden />
            ) : (
              <FlaskConicalIcon aria-hidden />
            )}
            Simulate
          </Button>
        </form>

        {simulate.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(simulate.error)}</AlertDescription>
          </Alert>
        ) : null}
        {result ? (
          <section className="space-y-3 rounded-lg border bg-muted/30 p-4" aria-label="Simulation result" aria-live="polite">
            <div className="flex flex-wrap items-center gap-2">
              <OutcomeBadge outcome={result.outcome} />
              {result.team ? (
                <span className="text-sm">
                  {result.team.name} team
                  {result.strategy ? ` · ${STRATEGY_LABELS[result.strategy]}` : ""}
                  {result.assignee ? ` · would go to ${result.assignee.name}` : ""}
                </span>
              ) : null}
            </div>
            <p className="text-sm">{result.explanation}</p>
            <DecisionDetails
              rules={result.rules}
              candidates={result.candidates}
              showSkills={result.strategy === "skill_match"}
            />
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}
