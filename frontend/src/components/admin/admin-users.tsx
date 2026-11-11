"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { EllipsisIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { SearchInput } from "@/components/search-input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { api, errorMessage, unwrap, type Schemas, type User } from "@/lib/api/client";
import { useCurrentUser } from "@/lib/auth/auth-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDate, initials } from "@/lib/roles";

const LIMIT = 20;

type Change = "deactivate" | "activate" | "grant" | "revoke";

const CHANGES: Record<
  Change,
  {
    menuLabel: string;
    title: (user: User) => string;
    description: string;
    confirmLabel: string;
    destructive: boolean;
    body: Schemas["AdminUserUpdate"];
    done: (user: User) => string;
  }
> = {
  deactivate: {
    menuLabel: "Deactivate account",
    title: (user) => `Deactivate ${user.full_name}?`,
    description:
      "They are signed out everywhere and can't sign in until reactivated. Their data is kept.",
    confirmLabel: "Deactivate",
    destructive: true,
    body: { is_active: false },
    done: (user) => `${user.full_name} was deactivated`,
  },
  activate: {
    menuLabel: "Reactivate account",
    title: (user) => `Reactivate ${user.full_name}?`,
    description: "They can sign in again and regain access to their organizations.",
    confirmLabel: "Reactivate",
    destructive: false,
    body: { is_active: true },
    done: (user) => `${user.full_name} was reactivated`,
  },
  grant: {
    menuLabel: "Make platform admin",
    title: (user) => `Make ${user.full_name} a platform admin?`,
    description:
      "They will be able to manage every user and organization on the platform, including other admins.",
    confirmLabel: "Make admin",
    destructive: true,
    body: { is_superadmin: true },
    done: (user) => `${user.full_name} is now a platform admin`,
  },
  revoke: {
    menuLabel: "Remove platform admin",
    title: (user) => `Remove ${user.full_name}'s platform admin access?`,
    description: "Their organization memberships are not affected.",
    confirmLabel: "Remove access",
    destructive: true,
    body: { is_superadmin: false },
    done: (user) => `${user.full_name} is no longer a platform admin`,
  },
};

function availableChanges(user: User): Change[] {
  return [user.is_active ? "deactivate" : "activate", user.is_superadmin ? "revoke" : "grant"];
}

export function AdminUsers() {
  const me = useCurrentUser();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebouncedValue(search.trim());
  const page = { limit: LIMIT, offset, search: debouncedSearch || undefined };

  const users = useQuery({
    queryKey: queryKeys.adminUsers(page),
    queryFn: () => unwrap(api.GET("/api/v1/admin/users", { params: { query: page } })),
    placeholderData: keepPreviousData,
  });

  // `target` outlives `open` so the dialog's text doesn't vanish while it closes.
  const [target, setTarget] = useState<{ user: User; change: Change } | null>(null);
  const [open, setOpen] = useState(false);

  const apply = async () => {
    if (!target) return;
    const { user, change } = target;
    try {
      await unwrap(
        api.PATCH("/api/v1/admin/users/{user_id}", {
          params: { path: { user_id: user.id } },
          body: CHANGES[change].body,
        }),
      );
      toast.success(CHANGES[change].done(user));
      await queryClient.invalidateQueries({ queryKey: queryKeys.adminUsers() });
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  return (
    <>
      <PageHeader title="All users" description="Every account on the platform." />
      <Card>
        <CardContent className="space-y-4">
          <SearchInput
            className="max-w-sm"
            placeholder="Search by name or email"
            aria-label="Search users"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setOffset(0);
            }}
          />
          {users.isPending ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : users.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(users.error)}</AlertDescription>
            </Alert>
          ) : users.data.items.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {debouncedSearch ? `No users match “${debouncedSearch}”.` : "No users yet."}
            </p>
          ) : (
            <>
              <Table aria-busy={users.isFetching}>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="hidden sm:table-cell">Platform role</TableHead>
                    <TableHead className="hidden md:table-cell">Joined</TableHead>
                    <TableHead className="w-12">
                      <span className="sr-only">Actions</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.data.items.map((user) => (
                    <TableRow key={user.id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                            {initials(user.full_name)}
                          </span>
                          <div className="min-w-0">
                            <div className="truncate font-medium">
                              {user.full_name}
                              {user.id === me.id ? (
                                <span className="ml-1 text-muted-foreground">(you)</span>
                              ) : null}
                            </div>
                            <div className="truncate text-xs text-muted-foreground">
                              {user.email}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        {user.is_active ? (
                          <Badge variant="secondary">Active</Badge>
                        ) : (
                          <Badge variant="destructive">Deactivated</Badge>
                        )}
                      </TableCell>
                      <TableCell className="hidden sm:table-cell">
                        {user.is_superadmin ? (
                          <Badge>Platform admin</Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="hidden text-muted-foreground md:table-cell">
                        {formatDate(user.created_at)}
                      </TableCell>
                      <TableCell>
                        {user.id === me.id ? null : (
                          <DropdownMenu>
                            <DropdownMenuTrigger
                              render={
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  aria-label={`Actions for ${user.full_name}`}
                                />
                              }
                            >
                              <EllipsisIcon aria-hidden />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="min-w-52">
                              {availableChanges(user).map((change) => (
                                <DropdownMenuItem
                                  key={change}
                                  variant={CHANGES[change].destructive ? "destructive" : "default"}
                                  onClick={() => {
                                    setTarget({ user, change });
                                    setOpen(true);
                                  }}
                                >
                                  {CHANGES[change].menuLabel}
                                </DropdownMenuItem>
                              ))}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Pager
                total={users.data.total}
                limit={LIMIT}
                offset={offset}
                onOffsetChange={setOffset}
              />
            </>
          )}
        </CardContent>
      </Card>
      {target ? (
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title={CHANGES[target.change].title(target.user)}
          description={CHANGES[target.change].description}
          confirmLabel={CHANGES[target.change].confirmLabel}
          destructive={CHANGES[target.change].destructive}
          onConfirm={apply}
        />
      ) : null}
    </>
  );
}
