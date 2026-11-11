"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ConfirmAction } from "@/components/confirm-action";
import { InviteDialog } from "@/components/settings/invite-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, errorMessage, unwrap, type Invitation } from "@/lib/api/client";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { ROLE_LABELS, formatDate } from "@/lib/roles";

const PAGE = { limit: 100 };

/** Open invitations. Rendered for admins only (the API enforces the same). */
export function InvitationsPanel() {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();

  const invitations = useQuery({
    queryKey: queryKeys.invitations(organization.id, PAGE),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/organizations/{org_id}/invitations", {
          params: { path: { org_id: organization.id }, query: PAGE },
        }),
      ),
  });

  const revoke = async (invitation: Invitation) => {
    try {
      await unwrap(
        api.DELETE("/api/v1/organizations/{org_id}/invitations/{invitation_id}", {
          params: { path: { org_id: organization.id, invitation_id: invitation.id } },
        }),
      );
      toast.success(`Invitation for ${invitation.email} revoked`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.invitations(organization.id) });
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle><h2>Invitations</h2></CardTitle>
        <CardDescription>People invited to join who haven&apos;t accepted yet.</CardDescription>
        <CardAction>
          <InviteDialog />
        </CardAction>
      </CardHeader>
      <CardContent>
        {invitations.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : invitations.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(invitations.error)}</AlertDescription>
          </Alert>
        ) : invitations.data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No open invitations.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead className="hidden md:table-cell">Invited by</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead className="w-24"><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invitations.data.items.map((invitation) => (
                <TableRow key={invitation.id}>
                  <TableCell className="font-medium">{invitation.email}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{ROLE_LABELS[invitation.role]}</Badge>
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell">
                    {invitation.invited_by_name ?? "—"}
                  </TableCell>
                  <TableCell>
                    {invitation.status === "expired" ? (
                      <Badge variant="destructive">Expired</Badge>
                    ) : (
                      <span className="text-muted-foreground">{formatDate(invitation.expires_at)}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <ConfirmAction
                      triggerProps={{ variant: "ghost", size: "sm" }}
                      title={`Revoke the invitation for ${invitation.email}?`}
                      description="The link in their email will stop working. You can invite them again later."
                      confirmLabel="Revoke"
                      onConfirm={() => revoke(invitation)}
                    >
                      Revoke
                    </ConfirmAction>
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
