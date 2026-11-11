"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, errorMessage, unwrap, type Member, type OrgRole } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROLES, ROLE_LABELS, formatDate, initials } from "@/lib/roles";

const PAGE = { limit: 100 };

export function MembersPanel() {
  const organization = useActiveOrganization();
  const user = useCurrentUser();
  const queryClient = useQueryClient();
  const isAdmin = organization.role === "admin";
  const canView = organization.role !== "customer";

  const members = useQuery({
    queryKey: queryKeys.members(organization.id, PAGE),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/organizations/{org_id}/members", {
          params: { path: { org_id: organization.id }, query: PAGE },
        }),
      ),
    enabled: canView,
  });

  const refresh = async (member: Member) => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.members(organization.id) });
    // Changing your own membership changes what you can do here.
    if (member.user_id === user.id) {
      await refreshOrganizations(queryClient);
    }
  };

  const changeRole = useMutation({
    mutationFn: ({ member, role }: { member: Member; role: OrgRole }) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}/members/{member_id}", {
          params: { path: { org_id: organization.id, member_id: member.id } },
          body: { role },
        }),
      ),
    onSuccess: async (updated, { member }) => {
      toast.success(`${updated.full_name} is now ${ROLE_LABELS[updated.role].toLowerCase()}`);
      await refresh(member);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const removeMember = async (member: Member) => {
    try {
      await unwrap(
        api.DELETE("/api/v1/organizations/{org_id}/members/{member_id}", {
          params: { path: { org_id: organization.id, member_id: member.id } },
        }),
      );
      toast.success(`${member.full_name} was removed`);
      await refresh(member);
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle><h2>Members</h2></CardTitle>
        <CardDescription>
          {members.data ? `${members.data.total} ${members.data.total === 1 ? "person" : "people"} in ${organization.name}.` : "People in this organization."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!canView ? (
          <Alert>
            <AlertDescription>Only admins and agents can see the member list.</AlertDescription>
          </Alert>
        ) : members.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : members.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(members.error)}</AlertDescription>
          </Alert>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="hidden md:table-cell">Joined</TableHead>
                {isAdmin ? <TableHead className="w-12"><span className="sr-only">Actions</span></TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data.items.map((member) => (
                <TableRow key={member.id}>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                        {initials(member.full_name)}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate font-medium">
                          {member.full_name}
                          {member.user_id === user.id ? (
                            <span className="ml-1 text-muted-foreground">(you)</span>
                          ) : null}
                        </div>
                        <div className="truncate text-xs text-muted-foreground">{member.email}</div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    {isAdmin ? (
                      <Select
                        items={ROLE_LABELS}
                        value={member.role}
                        onValueChange={(role) => {
                          if (role && role !== member.role) changeRole.mutate({ member, role: role as OrgRole });
                        }}
                        disabled={changeRole.isPending}
                      >
                        <SelectTrigger size="sm" aria-label={`Role of ${member.full_name}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ROLES.map((role) => (
                            <SelectItem key={role} value={role}>
                              {ROLE_LABELS[role]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Badge variant="secondary">{ROLE_LABELS[member.role]}</Badge>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell">
                    {formatDate(member.created_at)}
                  </TableCell>
                  {isAdmin ? (
                    <TableCell>
                      <ConfirmAction
                        triggerProps={{
                          variant: "ghost",
                          size: "icon-sm",
                          "aria-label": `Remove ${member.full_name}`,
                        }}
                        title={`Remove ${member.full_name}?`}
                        description={
                          member.user_id === user.id
                            ? "You will lose access to this organization."
                            : `${member.full_name} will lose access to ${organization.name}.`
                        }
                        confirmLabel="Remove"
                        onConfirm={() => removeMember(member)}
                      >
                        <Trash2Icon aria-hidden />
                      </ConfirmAction>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
