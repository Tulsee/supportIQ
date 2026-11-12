"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CrownIcon, Loader2Icon, PencilIcon, PlusIcon, UsersIcon } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { SkillsEditor, cleanSkills } from "@/components/settings/skills-editor";
import { useAgents, useTeams } from "@/components/tickets/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage, tenant, unwrap, type Agent, type SkillLevel, type Team } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { PRESENCE } from "@/lib/routing";
import { formatDateTime, timeAgo } from "@/lib/tickets";
import { cn } from "@/lib/utils";

type TeamChange = { name?: string; description?: string; is_active?: boolean };
type Membership = "none" | "member" | "lead";
const MEMBERSHIP_LABELS: Record<Membership, string> = {
  none: "Not in the team",
  member: "Member",
  lead: "Lead",
};

function useRefresh() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.teams(organization.id), refetchType: "all" }),
      queryClient.invalidateQueries({ queryKey: queryKeys.agents(organization.id), refetchType: "all" }),
    ]);
}

function useTeamMutations() {
  const organization = useActiveOrganization();
  const refresh = useRefresh();
  const params = { header: tenant(organization.id) };
  const create = useMutation({
    mutationFn: (body: { name: string; description: string }) =>
      unwrap(api.POST("/api/v1/teams", { params, body })),
    onSuccess: (team) => {
      toast.success(`Added the ${team.name} team`);
      return refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: TeamChange }) =>
      unwrap(api.PATCH("/api/v1/teams/{team_id}", { params: { ...params, path: { team_id: id } }, body })),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const setMembers = useMutation({
    mutationFn: ({ id, members }: { id: string; members: { user_id: string; role: "member" | "lead" }[] }) =>
      unwrap(
        api.PUT("/api/v1/teams/{team_id}/members", {
          params: { ...params, path: { team_id: id } },
          body: { members },
        }),
      ),
    onSuccess: () => refresh(),
    onError: (error) => toast.error(errorMessage(error)),
  });
  return { create, update, setMembers };
}

// --- Teams -------------------------------------------------------------------------------

function AddTeam() {
  const uid = useId();
  const { create } = useTeamMutations();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(
      { name: name.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setName("");
          setDescription("");
        },
      },
    );
  }

  return (
    <form onSubmit={submit} noValidate>
      <FieldGroup className="gap-4 sm:grid sm:grid-cols-[14rem_1fr_auto] sm:items-end">
        <Field>
          <FieldLabel htmlFor={`${uid}-name`}>New team</FieldLabel>
          <Input
            id={`${uid}-name`}
            value={name}
            placeholder="e.g. Billing"
            onChange={(event) => setName(event.target.value)}
            maxLength={100}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor={`${uid}-description`}>Description (optional)</FieldLabel>
          <Input
            id={`${uid}-description`}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={300}
          />
        </Field>
        <Button type="submit" disabled={name.trim().length < 2 || create.isPending}>
          {create.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <PlusIcon aria-hidden />}
          Add team
        </Button>
      </FieldGroup>
    </form>
  );
}

function EditTeam({ team }: { team: Team }) {
  const uid = useId();
  const { update } = useTeamMutations();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description);

  function submit(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      { id: team.id, body: { name: name.trim(), description: description.trim() } },
      {
        onSuccess: () => {
          toast.success("Team updated");
          setOpen(false);
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setName(team.name);
          setDescription(team.description);
        }
      }}
    >
      <DialogTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Rename ${team.name}`} />}>
        <PencilIcon aria-hidden />
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} noValidate>
          <DialogHeader>
            <DialogTitle>Edit {team.name}</DialogTitle>
          </DialogHeader>
          <FieldGroup className="my-4 gap-4">
            <Field>
              <FieldLabel htmlFor={`${uid}-name`}>Name</FieldLabel>
              <Input id={`${uid}-name`} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
            </Field>
            <Field>
              <FieldLabel htmlFor={`${uid}-description`}>Description</FieldLabel>
              <Input
                id={`${uid}-description`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                maxLength={300}
              />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={name.trim().length < 2 || update.isPending}>
              {update.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TeamMembers({ team, staff }: { team: Team; staff: Agent[] }) {
  const { setMembers } = useTeamMutations();
  const [open, setOpen] = useState(false);
  const current = (): Record<string, Membership> =>
    Object.fromEntries(team.members.map((m) => [m.user_id, m.role]));
  const [roles, setRoles] = useState<Record<string, Membership>>(current);
  const chosen = Object.entries(roles).filter(([, role]) => role !== "none");

  function save() {
    setMembers.mutate(
      {
        id: team.id,
        members: chosen.map(([user_id, role]) => ({ user_id, role: role as "member" | "lead" })),
      },
      {
        onSuccess: () => {
          toast.success(`Saved the ${team.name} team`);
          setOpen(false);
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setRoles(current());
      }}
    >
      <DialogTrigger render={<Button variant="outline" size="sm" aria-label={`Members of ${team.name}`} />}>
        <UsersIcon aria-hidden />
        Members
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Members of {team.name}</DialogTitle>
          <DialogDescription>
            Routing hands the team&apos;s tickets to its members. Leads also get its SLA alerts and
            hear when nobody can take a ticket.
          </DialogDescription>
        </DialogHeader>
        <ul className="my-2 max-h-[50vh] divide-y overflow-y-auto" aria-label="Admins and agents">
          {staff.map((person) => (
            <li key={person.user_id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{person.full_name}</div>
                <div className="truncate text-xs text-muted-foreground">{person.email}</div>
              </div>
              <Select
                items={MEMBERSHIP_LABELS}
                value={roles[person.user_id] ?? "none"}
                onValueChange={(value) =>
                  value && setRoles((all) => ({ ...all, [person.user_id]: value as Membership }))
                }
              >
                <SelectTrigger aria-label={`${person.full_name} in ${team.name}`} className="w-40 shrink-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(MEMBERSHIP_LABELS) as Membership[]).map((value) => (
                    <SelectItem key={value} value={value}>
                      {MEMBERSHIP_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </li>
          ))}
        </ul>
        <DialogFooter className="items-center sm:justify-between">
          <span className="text-sm text-muted-foreground">
            {chosen.length} {chosen.length === 1 ? "member" : "members"}
          </span>
          <Button onClick={save} disabled={setMembers.isPending}>
            {setMembers.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
            Save members
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MemberList({ team }: { team: Team }) {
  if (team.members.length === 0) {
    return <span className="text-sm text-muted-foreground">No members yet</span>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={`Members of ${team.name}`}>
      {team.members.map((member) => (
        <li key={member.user_id}>
          <Badge variant={member.role === "lead" ? "secondary" : "outline"} className="gap-1 font-normal">
            {member.role === "lead" ? <CrownIcon className="size-3" aria-label="Lead:" /> : null}
            {member.full_name}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function TeamsCard({ staff }: { staff: Agent[] }) {
  const { teams, isPending } = useTeams(true);
  const { update } = useTeamMutations();
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>
          <h2>Teams</h2>
        </CardTitle>
        <CardDescription>
          Routing rules send tickets to a team. Teams are turned off rather than deleted, so tickets keep
          theirs; members stay for when a team comes back.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <AddTeam />
        {isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : teams.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No teams yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-48">Team</TableHead>
                <TableHead>Members</TableHead>
                <TableHead className="w-20">Active</TableHead>
                <TableHead className="w-40">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {teams.map((team) => (
                <TableRow key={team.id}>
                  <TableCell className="whitespace-normal align-top">
                    <div className="flex flex-wrap items-center gap-2 font-medium">
                      {team.name}
                      {team.is_active ? null : <Badge variant="outline">Off</Badge>}
                    </div>
                    {team.description ? (
                      <div className="text-xs text-muted-foreground">{team.description}</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="whitespace-normal align-top">
                    <MemberList team={team} />
                  </TableCell>
                  <TableCell className="align-top">
                    <Switch
                      checked={team.is_active}
                      onCheckedChange={(checked) => update.mutate({ id: team.id, body: { is_active: checked } })}
                      aria-label={`${team.name} active`}
                    />
                  </TableCell>
                  <TableCell className="align-top">
                    <div className="flex items-center justify-end gap-1">
                      <TeamMembers team={team} staff={staff} />
                      <EditTeam team={team} />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

// --- Agents ------------------------------------------------------------------------------

export function PresenceBadge({ agent }: { agent: Agent }) {
  const presence = PRESENCE[agent.presence];
  const title = agent.online
    ? `Seen ${timeAgo(agent.last_seen_at!)}`
    : agent.status !== "available"
      ? `Status: ${PRESENCE[agent.status].label.toLowerCase()} (app closed)`
      : "App closed";
  return (
    <span className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap" title={title}>
      <span className={cn("size-2 shrink-0 rounded-full", presence.dot)} aria-hidden />
      {presence.label}
    </span>
  );
}

function Workload({ agent }: { agent: Agent }) {
  const capacity = agent.max_active_tickets;
  const share = capacity ? Math.min(agent.active_tickets / capacity, 1) : 1;
  const full = capacity === 0 || agent.active_tickets >= capacity;
  return (
    <div className="w-28 space-y-1">
      <div className="text-sm tabular-nums">
        {capacity === 0 ? (
          <span className="text-muted-foreground">No routing</span>
        ) : (
          <>
            {agent.active_tickets} <span className="text-muted-foreground">of {capacity}</span>
          </>
        )}
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-label={`${agent.full_name}'s workload`}
        aria-valuemin={0}
        aria-valuemax={capacity}
        aria-valuenow={agent.active_tickets}
      >
        <div
          className={cn("h-full rounded-full", full ? "bg-amber-500" : "bg-primary")}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}

function EditAgent({ agent, knownSkills }: { agent: Agent; knownSkills: string[] }) {
  const uid = useId();
  const organization = useActiveOrganization();
  const refresh = useRefresh();
  const [open, setOpen] = useState(false);
  const [capacity, setCapacity] = useState(String(agent.max_active_tickets));
  const [skills, setSkills] = useState<SkillLevel[]>(agent.skills);
  const save = useMutation({
    mutationFn: (body: { max_active_tickets: number; skills: SkillLevel[] }) =>
      unwrap(
        api.PUT("/api/v1/agents/{user_id}/profile", {
          params: { path: { user_id: agent.user_id }, header: tenant(organization.id) },
          body,
        }),
      ),
    onSuccess: async () => {
      await refresh();
      toast.success(`Saved ${agent.full_name}'s profile`);
      setOpen(false);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const max = Number(capacity);
  const validCapacity = capacity.trim() !== "" && Number.isInteger(max) && max >= 0 && max <= 200;
  const cleaned = cleanSkills(skills);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setCapacity(String(agent.max_active_tickets));
          setSkills(agent.skills);
        }
      }}
    >
      <DialogTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Edit ${agent.full_name}`} />}>
        <PencilIcon aria-hidden />
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (validCapacity && cleaned) save.mutate({ max_active_tickets: max, skills: cleaned });
          }}
          noValidate
        >
          <DialogHeader>
            <DialogTitle>{agent.full_name}</DialogTitle>
            <DialogDescription>How much routing gives them, and what they&apos;re good at.</DialogDescription>
          </DialogHeader>
          <FieldGroup className="my-4 gap-5">
            <Field data-invalid={!validCapacity}>
              <FieldLabel htmlFor={`${uid}-capacity`}>Capacity</FieldLabel>
              <div className="flex items-center gap-2">
                <Input
                  id={`${uid}-capacity`}
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={200}
                  className="w-24"
                  value={capacity}
                  aria-invalid={!validCapacity}
                  aria-describedby={`${uid}-capacity-help`}
                  onChange={(event) => setCapacity(event.target.value)}
                />
                <span className="text-sm text-muted-foreground">active tickets</span>
              </div>
              <FieldDescription id={`${uid}-capacity-help`}>
                Assigned or in progress; tickets waiting for the customer don&apos;t count. 0 means no
                automatic assignments (people can still assign them).
              </FieldDescription>
            </Field>
            <Field>
              <FieldLabel id={`${uid}-skills`}>Skills</FieldLabel>
              <SkillsEditor
                value={skills}
                onChange={setSkills}
                suggestions={knownSkills}
                label={`${agent.full_name}'s skills`}
              />
              <FieldDescription>
                Levels from 1 (basic) to 5 (expert). Rules can require a skill at a minimum level.
              </FieldDescription>
              {cleaned === null ? (
                <p className="text-sm text-destructive" role="alert">
                  Use lower-case words joined by - or _ (like <code>refunds</code>), each skill once.
                </p>
              ) : null}
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="submit" disabled={!validCapacity || cleaned === null || save.isPending}>
              {save.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AgentsCard({ agents, isPending }: { agents: Agent[]; isPending: boolean }) {
  const knownSkills = [...new Set(agents.flatMap((a) => a.skills.map((s) => s.skill)))].sort();
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>Agents</h2>
        </CardTitle>
        <CardDescription>
          Routing only picks agents who are available and have the app open, have room under their
          capacity, and have the skills a rule asks for. Everyone sets their own status in the menu at
          the bottom left.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="w-28">Status</TableHead>
                <TableHead className="w-32">Workload</TableHead>
                <TableHead className="hidden md:table-cell">Skills</TableHead>
                <TableHead className="hidden lg:table-cell">Teams</TableHead>
                <TableHead className="w-12">
                  <span className="sr-only">Edit</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {agents.map((agent) => (
                <TableRow key={agent.user_id}>
                  <TableCell className="whitespace-normal">
                    <div className="font-medium">{agent.full_name}</div>
                    <div className="text-xs text-muted-foreground">
                      {agent.role === "admin" ? "Admin" : "Agent"}
                      {agent.status_changed_at ? ` · status set ${formatDateTime(agent.status_changed_at)}` : ""}
                    </div>
                  </TableCell>
                  <TableCell>
                    <PresenceBadge agent={agent} />
                  </TableCell>
                  <TableCell>
                    <Workload agent={agent} />
                  </TableCell>
                  <TableCell className="hidden whitespace-normal md:table-cell">
                    {agent.skills.length ? (
                      <ul className="flex flex-wrap gap-1" aria-label={`${agent.full_name}'s skills`}>
                        {agent.skills.map((skill) => (
                          <li key={skill.skill}>
                            <Badge variant="outline" className="font-mono font-normal">
                              {skill.skill} <span className="text-muted-foreground">{skill.level}</span>
                            </Badge>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="text-sm text-muted-foreground">None</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-sm whitespace-normal lg:table-cell">
                    {agent.teams.length
                      ? agent.teams.map((t) => (t.role === "lead" ? `${t.name} (lead)` : t.name)).join(", ")
                      : <span className="text-muted-foreground">None</span>}
                  </TableCell>
                  <TableCell>
                    <EditAgent agent={agent} knownSkills={knownSkills} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

/** Admins: teams with members and leads, and each agent's capacity and skills. */
export function TeamsPage() {
  const { agents, isPending } = useAgents();
  return (
    <>
      <PageHeader
        title="Teams and agents"
        description="Who handles which tickets, how many each agent takes on, and what they're good at."
      />
      <TeamsCard staff={agents} />
      <AgentsCard agents={agents} isPending={isPending} />
    </>
  );
}
