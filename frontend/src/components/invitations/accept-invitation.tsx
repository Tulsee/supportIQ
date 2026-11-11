"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2Icon, MailIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { browser } from "react-dom";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, errorMessage, unwrap } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/auth-provider";
import {
  clearPendingInvitation,
  readPendingInvitation,
  savePendingInvitation,
} from "@/lib/invitations/pending";
import { refreshOrganizations, rememberActiveOrganization } from "@/lib/org/org-provider";
import { ROLE_LABELS } from "@/lib/roles";

/** The token arrives in the URL fragment (#token=...), which is never sent to a
 * server; after a sign-in detour it comes from the pending invitation instead. */
function readToken(): string | null {
  const fromUrl = new URLSearchParams(window.location.hash.slice(1)).get("token");
  return fromUrl ?? readPendingInvitation()?.token ?? null;
}

const STATUS_MESSAGES = {
  accepted: "This invitation has already been used.",
  revoked: "This invitation was withdrawn by the organization.",
  expired: "This invitation has expired. Ask an admin to send you a new one.",
} as const;

export function AcceptInvitation() {
  // The token only exists in the browser (URL fragment / sessionStorage), so the
  // first browser render can't match prerendered HTML: render only in the browser.
  use(browser("The invitation token is in the URL fragment, which only the browser can read."));
  const router = useRouter();
  const queryClient = useQueryClient();
  const auth = useAuth();
  const [token] = useState(readToken);

  useEffect(() => {
    if (!token) return;
    const pending = readPendingInvitation();
    savePendingInvitation(pending?.token === token ? pending : { token });
    // Drop the token from the address bar and history so it isn't shared by accident.
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, [token]);

  const preview = useQuery({
    queryKey: ["invitation-preview", token],
    queryFn: () => unwrap(api.POST("/api/v1/invitations/lookup", { body: { token: token! } })),
    enabled: !!token,
  });

  // A used, withdrawn, or expired invitation shouldn't keep redirecting future sign-ins here.
  const unusable = preview.data && preview.data.status !== "pending";
  useEffect(() => {
    if (unusable) clearPendingInvitation();
  }, [unusable]);

  const accept = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/invitations/accept", { body: { token: token! } })),
    onSuccess: async (organization) => {
      clearPendingInvitation();
      rememberActiveOrganization(organization.id);
      await refreshOrganizations(queryClient);
      toast.success(`You joined ${organization.name}`);
      router.replace("/dashboard");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "already_member") {
        clearPendingInvitation();
        router.replace("/dashboard");
      }
    },
  });

  if ((token && preview.isPending) || auth.status === "loading") {
    return <Skeleton className="h-72 w-full rounded-xl" />;
  }

  if (!token || preview.isError) {
    const notFound = !token || (preview.error instanceof ApiError && preview.error.status === 404);
    return (
      <InvitationProblem
        title={notFound ? "Invitation not found" : "Couldn't load the invitation"}
        message={
          notFound
            ? "This link is invalid or incomplete. Open the link from your invitation email again."
            : errorMessage(preview.error)
        }
      />
    );
  }

  const invitation = preview.data!;
  if (invitation.status !== "pending") {
    return <InvitationProblem title="Invitation unavailable" message={STATUS_MESSAGES[invitation.status]} />;
  }

  const signedInAs = auth.user?.email;
  const goToAuth = (path: "/login" | "/register") => {
    savePendingInvitation({ token, email: invitation.email });
    router.push(path);
  };

  return (
    <Card>
      <CardHeader>
        <div className="mb-2 flex size-10 items-center justify-center rounded-full bg-primary/10 text-primary">
          <MailIcon className="size-5" aria-hidden />
        </div>
        <CardTitle className="text-xl"><h1>Join {invitation.organization_name}</h1></CardTitle>
        <CardDescription>
          {invitation.invited_by_name ?? "An admin"} invited <strong>{invitation.email}</strong> to
          join as <strong>{ROLE_LABELS[invitation.role]}</strong>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {signedInAs && signedInAs !== invitation.email ? (
          <Alert>
            <AlertTitle>You&apos;re signed in as {signedInAs}</AlertTitle>
            <AlertDescription>
              This invitation is for {invitation.email}. Sign out, then sign in with that address.
            </AlertDescription>
          </Alert>
        ) : null}
        {accept.isError && !(accept.error instanceof ApiError && accept.error.code === "already_member") ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(accept.error)}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
      <CardFooter className="flex flex-col gap-2">
        {!signedInAs ? (
          invitation.account_exists ? (
            <Button size="lg" className="w-full" onClick={() => goToAuth("/login")}>
              Sign in to accept
            </Button>
          ) : (
            <Button size="lg" className="w-full" onClick={() => goToAuth("/register")}>
              Create account to accept
            </Button>
          )
        ) : signedInAs === invitation.email ? (
          <Button size="lg" className="w-full" disabled={accept.isPending} onClick={() => accept.mutate()}>
            {accept.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
            Accept invitation
          </Button>
        ) : (
          <Button size="lg" variant="outline" className="w-full" onClick={() => auth.logout()}>
            Sign out
          </Button>
        )}
        <p className="text-xs text-muted-foreground">
          Expires {new Date(invitation.expires_at).toLocaleString()}.
        </p>
      </CardFooter>
    </Card>
  );
}

function InvitationProblem({ title, message }: { title: string; message: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl"><h1>{title}</h1></CardTitle>
        <CardDescription>{message}</CardDescription>
      </CardHeader>
      <CardFooter>
        <Link href="/" className={buttonVariants({ variant: "outline", className: "w-full" })}>
          Go to SupportIQ
        </Link>
      </CardFooter>
    </Card>
  );
}
