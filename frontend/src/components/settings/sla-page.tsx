"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage, tenant, unwrap, type Schemas, type TicketPriority } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import {
  WEEKDAYS,
  browserTimeZone,
  formatInZone,
  formatMinutes,
  parseDuration,
  timeZones,
} from "@/lib/sla";
import { PRIORITIES, PRIORITY_LABELS } from "@/lib/tickets";

type CalendarRead = Schemas["CalendarRead"];
type PolicyRead = Schemas["PolicyRead"];
type Interval = { start: string; end: string };

const hhmm = (value: string) => value.slice(0, 5);

function useSla() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const params = { header: tenant(organization.id) };
  const key = queryKeys.sla(organization.id);
  const config = useQuery({
    queryKey: key,
    queryFn: () => unwrap(api.GET("/api/v1/sla", { params })),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: key, refetchType: "all" });
  return { organization, params, config, refresh };
}

function SetUp({ onDone }: { onDone: () => Promise<unknown> }) {
  const uid = useId();
  const { params } = useSla();
  const [timezone, setTimezone] = useState(browserTimeZone);
  const setUp = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/sla/setup", { params, body: { timezone } })),
    onSuccess: async () => {
      toast.success("Service levels set up");
      await onDone();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>
          <h2>Set up service levels</h2>
        </CardTitle>
        <CardDescription>
          Response and resolution targets per priority, counted in business hours. You start
          with Monday to Friday, 09:00 to 17:00, and these targets, and can change all of them:
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="grid gap-1 text-sm sm:grid-cols-2">
          <li>Urgent: first response 1 h, resolution 8 h</li>
          <li>High: 2 h and 16 h</li>
          <li>Normal: 4 h and 24 h</li>
          <li>Low: 8 h and 40 h</li>
        </ul>
        <Field>
          <FieldLabel htmlFor={`${uid}-tz`}>Time zone</FieldLabel>
          <Input
            id={`${uid}-tz`}
            list={`${uid}-zones`}
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            className="max-w-xs"
          />
          <datalist id={`${uid}-zones`}>
            {timeZones().map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
          <FieldDescription>Business hours are in this time zone.</FieldDescription>
        </Field>
      </CardContent>
      <CardFooter className="border-t">
        <Button onClick={() => setUp.mutate()} disabled={!timezone.trim() || setUp.isPending}>
          {setUp.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
          Set up service levels
        </Button>
      </CardFooter>
    </Card>
  );
}

function initialHours(calendar: CalendarRead): Record<number, Interval[]> {
  const byDay: Record<number, Interval[]> = {};
  for (const { day } of WEEKDAYS) byDay[day] = [];
  for (const h of calendar.hours) byDay[h.weekday].push({ start: hhmm(h.start), end: hhmm(h.end) });
  return byDay;
}

function BusinessHours({ calendar, onSaved }: { calendar: CalendarRead; onSaved: () => Promise<unknown> }) {
  const uid = useId();
  const { params } = useSla();
  const [timezone, setTimezone] = useState(calendar.timezone);
  const [hours, setHours] = useState(() => initialHours(calendar));
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/sla/calendar", {
          params,
          body: {
            timezone,
            hours: WEEKDAYS.flatMap(({ day }) =>
              hours[day].map((i) => ({ weekday: day, start: i.start, end: i.end })),
            ),
          },
        }),
      ),
    onSuccess: async () => {
      toast.success("Business hours saved");
      await onSaved();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const setDay = (day: number, intervals: Interval[]) => setHours((h) => ({ ...h, [day]: intervals }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Business hours</h2>
        </CardTitle>
        <CardDescription>SLA clocks only run during these hours. An end time of 00:00 means midnight.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field className="max-w-xs">
          <FieldLabel htmlFor={`${uid}-tz`}>Time zone</FieldLabel>
          <Input id={`${uid}-tz`} list={`${uid}-zones`} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          <datalist id={`${uid}-zones`}>
            {timeZones().map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </Field>
        <ul className="divide-y" aria-label="Working hours by day">
          {WEEKDAYS.map(({ day, label }) => {
            const intervals = hours[day];
            return (
              <li key={day} className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-start">
                <div className="flex w-36 shrink-0 items-center gap-2 pt-1">
                  <Switch
                    checked={intervals.length > 0}
                    onCheckedChange={(on) => setDay(day, on ? [{ start: "09:00", end: "17:00" }] : [])}
                    aria-label={`${label}: working day`}
                  />
                  <span className="text-sm font-medium">{label}</span>
                </div>
                {intervals.length === 0 ? (
                  <span className="pt-1 text-sm text-muted-foreground">Closed</span>
                ) : (
                  <div className="space-y-2">
                    {intervals.map((interval, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <Input
                          type="time"
                          className="w-36"
                          value={interval.start}
                          aria-label={`${label} interval ${index + 1} starts`}
                          onChange={(e) =>
                            setDay(day, intervals.map((x, i) => (i === index ? { ...x, start: e.target.value } : x)))
                          }
                        />
                        <span className="text-sm text-muted-foreground">to</span>
                        <Input
                          type="time"
                          className="w-36"
                          value={interval.end}
                          aria-label={`${label} interval ${index + 1} ends`}
                          onChange={(e) =>
                            setDay(day, intervals.map((x, i) => (i === index ? { ...x, end: e.target.value } : x)))
                          }
                        />
                        {intervals.length > 1 ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Remove ${label} interval ${index + 1}`}
                            onClick={() => setDay(day, intervals.filter((_, i) => i !== index))}
                          >
                            <Trash2Icon aria-hidden />
                          </Button>
                        ) : null}
                      </div>
                    ))}
                    {intervals.length < 4 ? (
                      <Button
                        variant="link"
                        size="xs"
                        className="h-auto p-0"
                        onClick={() => setDay(day, [...intervals, { start: "13:00", end: "17:00" }])}
                      >
                        Add a break (another interval)
                      </Button>
                    ) : null}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
      <CardFooter className="justify-end border-t">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
          Save business hours
        </Button>
      </CardFooter>
    </Card>
  );
}

function Holidays({ calendar, onChanged }: { calendar: CalendarRead; onChanged: () => Promise<unknown> }) {
  const uid = useId();
  const { params } = useSla();
  const [date, setDate] = useState("");
  const [name, setName] = useState("");
  const add = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/sla/calendar/holidays", { params, body: { date, name } })),
    onSuccess: async () => {
      setDate("");
      setName("");
      await onChanged();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/sla/calendar/holidays/{holiday_id}", { params: { ...params, path: { holiday_id: id } } })),
    onSuccess: () => onChanged(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate();
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Holidays</h2>
        </CardTitle>
        <CardDescription>No business time on these dates.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {calendar.holidays.length ? (
          <ul className="divide-y" aria-label="Holidays">
            {calendar.holidays.map((holiday) => (
              <li key={holiday.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                <span>
                  <span className="font-medium tabular-nums">{holiday.date}</span> · {holiday.name}
                </span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove ${holiday.name}`}
                  onClick={() => remove.mutate(holiday.id)}
                  disabled={remove.isPending}
                >
                  <Trash2Icon aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No holidays yet.</p>
        )}
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <Field className="w-40">
            <FieldLabel htmlFor={`${uid}-date`}>Date</FieldLabel>
            <Input id={`${uid}-date`} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field className="min-w-40 flex-1">
            <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
            <Input id={`${uid}-name`} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </Field>
          <Button type="submit" variant="outline" disabled={!date || !name.trim() || add.isPending}>
            <PlusIcon aria-hidden />
            Add holiday
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function PolicyRow({ policy, onSaved }: { policy: PolicyRead; onSaved: () => Promise<unknown> }) {
  const { params } = useSla();
  const label = policy.priority ? PRIORITY_LABELS[policy.priority] : "Default";
  const [first, setFirst] = useState(formatMinutes(policy.first_response_minutes));
  const [resolution, setResolution] = useState(formatMinutes(policy.resolution_minutes));
  const [warning, setWarning] = useState(formatMinutes(policy.warning_minutes));
  const parsed = { first: parseDuration(first), resolution: parseDuration(resolution), warning: parseDuration(warning) };
  const valid = parsed.first !== null && parsed.resolution !== null && parsed.warning !== null;
  const dirty =
    parsed.first !== policy.first_response_minutes ||
    parsed.resolution !== policy.resolution_minutes ||
    parsed.warning !== policy.warning_minutes;
  const update = useMutation({
    mutationFn: (body: Schemas["PolicyUpdate"]) =>
      unwrap(api.PATCH("/api/v1/sla/policies/{policy_id}", { params: { ...params, path: { policy_id: policy.id } }, body })),
    onSuccess: async (updated) => {
      toast.success(`${label} targets saved`);
      setFirst(formatMinutes(updated.first_response_minutes));
      setResolution(formatMinutes(updated.resolution_minutes));
      setWarning(formatMinutes(updated.warning_minutes));
      await onSaved();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const input = (value: string, set: (v: string) => void, name: string) => (
    <Input
      value={value}
      onChange={(e) => set(e.target.value)}
      aria-label={`${label}: ${name}`}
      aria-invalid={parseDuration(value) === null}
      className="w-28"
      disabled={!policy.is_active}
    />
  );
  return (
    <TableRow>
      <TableCell className="font-medium">
        {label}
        {policy.priority ? null : (
          <div className="text-xs font-normal text-muted-foreground">Priorities without their own</div>
        )}
      </TableCell>
      <TableCell>{input(first, setFirst, "first response")}</TableCell>
      <TableCell>{input(resolution, setResolution, "resolution")}</TableCell>
      <TableCell className="hidden md:table-cell">{input(warning, setWarning, "warn before")}</TableCell>
      <TableCell>
        <Switch
          checked={policy.is_active}
          onCheckedChange={(on) => update.mutate({ is_active: on })}
          aria-label={`${label} policy in use`}
        />
      </TableCell>
      <TableCell className="text-right">
        <Button
          size="sm"
          variant="outline"
          disabled={!valid || !dirty || update.isPending || !policy.is_active}
          onClick={() =>
            update.mutate({
              first_response_minutes: parsed.first ?? undefined,
              resolution_minutes: parsed.resolution ?? undefined,
              warning_minutes: parsed.warning ?? undefined,
            })
          }
        >
          Save
        </Button>
      </TableCell>
    </TableRow>
  );
}

function Policies({ policies, onSaved }: { policies: PolicyRead[]; onSaved: () => Promise<unknown> }) {
  const { params } = useSla();
  const hasDefault = policies.some((p) => p.priority === null && p.is_active);
  const addDefault = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/sla/policies", {
          params,
          body: { priority: null, first_response_minutes: 240, resolution_minutes: 1440, warning_minutes: 60 },
        }),
      ),
    onSuccess: () => onSaved(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Targets</h2>
        </CardTitle>
        <CardDescription>
          In business time, e.g. &ldquo;4 h&rdquo;, &ldquo;1 h 30 min&rdquo; or &ldquo;45 min&rdquo;. Changing a target applies to
          new tickets; tickets already open keep the targets they started with.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Priority</TableHead>
              <TableHead>First response</TableHead>
              <TableHead>Resolution</TableHead>
              <TableHead className="hidden md:table-cell">Warn before</TableHead>
              <TableHead>In use</TableHead>
              <TableHead>
                <span className="sr-only">Save</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {policies.map((policy) => (
              <PolicyRow key={`${policy.id}-${policy.version}-${policy.is_active}`} policy={policy} onSaved={onSaved} />
            ))}
          </TableBody>
        </Table>
      </CardContent>
      {hasDefault ? null : (
        <CardFooter className="border-t">
          <Button variant="outline" size="sm" onClick={() => addDefault.mutate()} disabled={addDefault.isPending}>
            <PlusIcon aria-hidden />
            Add a default policy
          </Button>
          <span className="ml-3 text-xs text-muted-foreground">
            Covers priorities whose own policy is turned off.
          </span>
        </CardFooter>
      )}
    </Card>
  );
}

function Preview({ calendar }: { calendar: CalendarRead }) {
  const { organization, params } = useSla();
  const [priority, setPriority] = useState<TicketPriority>("normal");
  const preview = useQuery({
    queryKey: [...queryKeys.sla(organization.id), "preview", priority],
    queryFn: () => unwrap(api.GET("/api/v1/sla/preview", { params: { ...params, query: { priority } } })),
  });
  const data = preview.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Try it</h2>
        </CardTitle>
        <CardDescription>When a ticket opened right now would be due (times in {calendar.timezone}).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Select items={PRIORITY_LABELS} value={priority} onValueChange={(v) => v && setPriority(v as TicketPriority)}>
          <SelectTrigger aria-label="Priority" className="w-40">
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
        {!data ? (
          <Skeleton className="h-16 w-full" />
        ) : !data.policy ? (
          <p className="text-muted-foreground">No policy covers this priority, so its tickets have no SLA.</p>
        ) : (
          <dl className="grid gap-2 sm:grid-cols-3" aria-live="polite">
            <div>
              <dt className="text-xs text-muted-foreground">Clock starts</dt>
              <dd className="font-medium">{formatInZone(data.clock_starts_at, data.timezone)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">First response by</dt>
              <dd className="font-medium">{formatInZone(data.first_response_due!, data.timezone)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Resolution by</dt>
              <dd className="font-medium">{formatInZone(data.resolution_due!, data.timezone)}</dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

/** Admins: business hours, holidays and SLA targets. */
export function SlaPage() {
  const { config, refresh } = useSla();
  const data = config.data;
  return (
    <>
      <PageHeader
        title="Service levels"
        description="How quickly tickets should get a first response and be resolved, counted in business hours."
        actions={data?.calendar ? <Badge variant="secondary">{data.calendar.timezone}</Badge> : null}
      />
      {config.error ? (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{errorMessage(config.error)}</AlertDescription>
        </Alert>
      ) : null}
      {!data ? (
        <Skeleton className="h-64 w-full max-w-2xl" />
      ) : !data.calendar ? (
        <SetUp onDone={refresh} />
      ) : (
        <div className="space-y-6">
          <Policies policies={data.policies} onSaved={refresh} />
          <div className="grid gap-6 xl:grid-cols-2">
            <div className="min-w-0">
              <BusinessHours
                key={data.calendar.timezone + data.calendar.hours.length}
                calendar={data.calendar}
                onSaved={refresh}
              />
            </div>
            <div className="min-w-0 space-y-6">
              <Preview calendar={data.calendar} />
              <Holidays calendar={data.calendar} onChanged={refresh} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
