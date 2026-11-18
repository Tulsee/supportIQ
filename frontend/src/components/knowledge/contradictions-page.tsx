"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, CheckIcon, Loader2Icon, RotateCcwIcon, ScanSearchIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { NoWorkerAlert, useWorkerOffline } from "@/components/knowledge/worker-status";
import { PageHeader } from "@/components/page-header";
import { Pager } from "@/components/pager";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  api,
  errorMessage,
  tenant,
  unwrap,
  type ContradictionFinding,
  type ContradictionScan,
  type FindingStatus,
} from "@/lib/api/client";
import { refreshOrganizations, useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";
import { formatDateTime, timeAgo } from "@/lib/tickets";

const LIMIT = 10;
const POLL_MS = 3_000;

const STATUS_FILTERS = {
  open: "Open",
  dismissed: "Dismissed",
  resolved: "Resolved",
  all: "All findings",
} as const;
type StatusFilter = keyof typeof STATUS_FILTERS;

const STATUS_BADGE: Record<FindingStatus, { label: string; variant: "destructive" | "secondary" | "outline" }> = {
  open: { label: "Open", variant: "destructive" },
  dismissed: { label: "Dismissed", variant: "outline" },
  resolved: { label: "Resolved", variant: "secondary" },
};

/** Passages in different documents that the model found to conflict, for admins to fix. */
export function ContradictionsPage() {
  const organization = useActiveOrganization();
  const isAdmin = organization.role === "admin";
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>("open");
  const [offset, setOffset] = useState(0);
  const query = { limit: LIMIT, offset, status: status === "all" ? undefined : status };

  const summary = useQuery({
    queryKey: queryKeys.contradictionSummary(organization.id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/knowledge/contradictions/summary", {
          params: { header: tenant(organization.id) },
        }),
      ),
    staleTime: 0,
    // A scan runs in the background: follow it until it settles.
    refetchInterval: (current) =>
      current.state.data?.latest_scan?.status === "running" ? POLL_MS : false,
  });
  const findings = useQuery({
    queryKey: queryKeys.contradictions(organization.id, query),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/knowledge/contradictions", {
          params: { query, header: tenant(organization.id) },
        }),
      ),
    placeholderData: keepPreviousData,
    staleTime: 0,
  });

  // When a scan finishes, its findings appear in the list.
  const latest = summary.data?.latest_scan;
  const running = latest?.status === "running";
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.contradictions(organization.id) });
    }
    wasRunning.current = running;
  }, [running, organization.id, queryClient]);
  const workerOffline = useWorkerOffline(running);

  const scan = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/knowledge/contradictions/scans", {
          params: { header: tenant(organization.id) },
        }),
      ),
    onSuccess: async () => {
      toast.success("Scan started");
      await queryClient.invalidateQueries({ queryKey: queryKeys.contradictionSummary(organization.id) });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <>
      <PageHeader
        title="Contradictions"
        description="Passages in different documents that give different answers to the same question. The assistant escalates questions that hit them, so fixing them helps every answer."
        actions={
          <>
            <Button variant="outline" render={<Link href="/knowledge-base" />}>
              <ArrowLeftIcon aria-hidden />
              Knowledge base
            </Button>
            {isAdmin ? (
              <Button onClick={() => scan.mutate()} disabled={scan.isPending || running}>
                {scan.isPending || running ? (
                  <Loader2Icon className="animate-spin" aria-hidden />
                ) : (
                  <ScanSearchIcon aria-hidden />
                )}
                {running ? "Scanning…" : "Scan now"}
              </Button>
            ) : null}
          </>
        }
      />
      <div className="space-y-6">
        {workerOffline ? <NoWorkerAlert /> : null}
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>Scans</h2>
            </CardTitle>
            <CardDescription>
              Each passage is compared with the most similar passages in other documents. The model must
              quote the conflicting sentence from both; a conflict it can&apos;t quote isn&apos;t reported.
              Pairs are checked once, and again only when their text changes.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {summary.isPending ? (
              <Skeleton className="h-16 w-full" />
            ) : summary.isError ? (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(summary.error)}</AlertDescription>
              </Alert>
            ) : (
              <>
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Open" value={summary.data.open} />
                  <Stat label="Resolved" value={summary.data.resolved} />
                  <Stat label="Dismissed" value={summary.data.dismissed} />
                  <Stat label="Pairs checked" value={summary.data.pairs_checked} />
                </dl>
                <ScanStatusLine scan={latest ?? null} />
                {isAdmin ? <AutoScanSwitch enabled={summary.data.scanning_enabled} /> : null}
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="space-y-4">
            <Select
              items={STATUS_FILTERS}
              value={status}
              onValueChange={(value) => {
                if (!value) return;
                setStatus(value as StatusFilter);
                setOffset(0);
              }}
            >
              <SelectTrigger aria-label="Status" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_FILTERS) as StatusFilter[]).map((value) => (
                  <SelectItem key={value} value={value}>
                    {STATUS_FILTERS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {findings.isPending ? (
              <div className="space-y-2">
                <Skeleton className="h-40 w-full" />
                <Skeleton className="h-40 w-full" />
              </div>
            ) : findings.isError ? (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(findings.error)}</AlertDescription>
              </Alert>
            ) : findings.data.items.length === 0 ? (
              <Empty className="py-12">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <ScanSearchIcon aria-hidden />
                  </EmptyMedia>
                  <EmptyTitle>
                    {status === "open" ? "No open contradictions" : "Nothing here"}
                  </EmptyTitle>
                  <EmptyDescription>
                    {summary.data && summary.data.pairs_checked === 0
                      ? isAdmin
                        ? "Run a scan to check the knowledge base."
                        : "No scan has run yet. An admin can start one."
                      : status === "open"
                        ? "Every pair checked so far agrees."
                        : `No ${STATUS_FILTERS[status].toLowerCase()} findings.`}
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <>
                <ul className="space-y-4" aria-busy={findings.isFetching}>
                  {findings.data.items.map((finding) => (
                    <li key={finding.id}>
                      <FindingCard finding={finding} isAdmin={isAdmin} />
                    </li>
                  ))}
                </ul>
                <Pager
                  total={findings.data.total}
                  limit={LIMIT}
                  offset={offset}
                  onOffsetChange={setOffset}
                />
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function ScanStatusLine({ scan }: { scan: ContradictionScan | null }) {
  if (!scan) return <p className="text-sm text-muted-foreground">No scan has run yet.</p>;
  const counts = `${scan.checked} pair${scan.checked === 1 ? "" : "s"} checked, ${scan.findings} new finding${scan.findings === 1 ? "" : "s"}`;
  const extras = [
    scan.failed ? `${scan.failed} couldn't be checked (tried again next time)` : null,
    scan.remaining ? `${scan.remaining} left for the next scan` : null,
    scan.auto_resolved ? `${scan.auto_resolved} resolved because a document changed` : null,
  ].filter(Boolean);
  const when = (
    <time dateTime={scan.started_at} title={formatDateTime(scan.started_at)}>
      {timeAgo(scan.started_at)}
    </time>
  );
  if (scan.status === "running") {
    return (
      <p className="flex items-center gap-2 text-sm" role="status">
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        <span>
          Scanning: {scan.checked} pair{scan.checked === 1 ? "" : "s"} checked so far, {scan.findings} new
          finding{scan.findings === 1 ? "" : "s"} (started {when}). Each pair takes the model a few seconds or
          more.
        </span>
      </p>
    );
  }
  if (scan.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          The last scan ({scan.trigger}, {when}) failed: {scan.error ?? "unknown error"}.
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <p className="text-sm text-muted-foreground">
      Last scan {when} ({scan.trigger}): {counts}
      {extras.length ? `; ${extras.join("; ")}` : ""}.
    </p>
  );
}

function AutoScanSwitch({ enabled }: { enabled: boolean }) {
  const id = useId();
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (on: boolean) =>
      unwrap(
        api.PATCH("/api/v1/organizations/{org_id}", {
          params: { path: { org_id: organization.id } },
          body: { settings: { contradiction_scan: on ? "on" : "off" } },
        }),
      ),
    onSuccess: async (updated) => {
      await refreshOrganizations(queryClient);
      await queryClient.invalidateQueries({ queryKey: queryKeys.contradictionSummary(organization.id) });
      toast.success(
        updated.settings.contradiction_scan === "on" ? "Scheduled scans on" : "Scheduled scans off",
      );
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <div className="flex items-center gap-2">
      <Switch id={id} checked={enabled} disabled={save.isPending} onCheckedChange={(on) => save.mutate(on)} />
      <Label htmlFor={id} className="font-normal">
        Scan automatically (new and changed passages, a few times a day)
      </Label>
    </div>
  );
}

function FindingCard({ finding, isAdmin }: { finding: ContradictionFinding; isAdmin: boolean }) {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const badge = STATUS_BADGE[finding.status];
  const review = useMutation({
    mutationFn: (status: FindingStatus) =>
      unwrap(
        api.PATCH("/api/v1/knowledge/contradictions/{finding_id}", {
          params: { path: { finding_id: finding.id }, header: tenant(organization.id) },
          body: { status },
        }),
      ),
    onSuccess: async (updated) => {
      toast.success(
        updated.status === "open" ? "Reopened" : updated.status === "dismissed" ? "Dismissed" : "Marked resolved",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.contradictions(organization.id) });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const quotes = (side: "a" | "b") =>
    finding.conflicts.filter((c) => c.grounded).map((c) => (side === "a" ? c.quote_a : c.quote_b));
  const topics = Array.from(new Set(finding.conflicts.map((c) => c.topic).filter(Boolean)));

  return (
    <article className="rounded-lg border p-4" aria-label={topics.join(", ") || "Contradiction"}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h3 className="font-medium">{topics.join(" · ") || "Conflicting passages"}</h3>
          <p className="text-xs text-muted-foreground">
            Found{" "}
            <time dateTime={finding.created_at} title={formatDateTime(finding.created_at)}>
              {timeAgo(finding.created_at)}
            </time>{" "}
            · model confidence {Math.round(finding.confidence * 100)}%
            {finding.model ? ` · ${finding.model}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={badge.variant}>{badge.label}</Badge>
          {isAdmin ? (
            finding.status === "open" ? (
              <>
                <Button size="sm" variant="outline" disabled={review.isPending} onClick={() => review.mutate("resolved")}>
                  <CheckIcon aria-hidden />
                  Resolved
                </Button>
                <Button size="sm" variant="ghost" disabled={review.isPending} onClick={() => review.mutate("dismissed")}>
                  <XIcon aria-hidden />
                  Dismiss
                </Button>
              </>
            ) : (
              <Button size="sm" variant="ghost" disabled={review.isPending} onClick={() => review.mutate("open")}>
                <RotateCcwIcon aria-hidden />
                Reopen
              </Button>
            )
          ) : null}
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <PassageView label="A" passage={finding.a} quotes={quotes("a")} />
        <PassageView label="B" passage={finding.b} quotes={quotes("b")} />
      </div>
      {finding.conflicts.length ? (
        <ul className="mt-3 space-y-1 text-sm">
          {finding.conflicts.map((conflict, index) => (
            <li key={index}>
              {conflict.explanation}
              {conflict.grounded ? null : (
                <span className="text-muted-foreground"> (quotes not found in the passages)</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {finding.status !== "open" ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {finding.status_reason === "document_changed"
            ? "Resolved automatically: one of the passages changed."
            : `${STATUS_BADGE[finding.status].label}${finding.reviewed_by ? ` by ${finding.reviewed_by.full_name}` : ""}`}
          {finding.reviewed_at ? ` ${timeAgo(finding.reviewed_at)}` : ""}
          {finding.note ? `: ${finding.note}` : ""}
        </p>
      ) : null}
    </article>
  );
}

function PassageView({
  label,
  passage,
  quotes,
}: {
  label: string;
  passage: ContradictionFinding["a"];
  quotes: string[];
}) {
  return (
    <div className="rounded-md bg-muted/40 p-3">
      <p className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
          {label}
        </Badge>
        <Link href={`/knowledge-base/${passage.document_id}`} className="font-medium text-foreground hover:underline">
          {passage.document_title || "Deleted document"}
        </Link>
        {passage.section ? <span>› {passage.section}</span> : null}
        {passage.current ? null : <Badge variant="secondary">Changed since</Badge>}
      </p>
      <p className="text-sm whitespace-pre-line">{highlight(passage.content, quotes)}</p>
    </div>
  );
}

/** The passage with each quote marked (exact matches, ignoring case and spacing). */
function highlight(text: string, quotes: string[]): ReactNode {
  const ranges: [number, number][] = [];
  const lower = text.toLowerCase();
  for (const quote of quotes) {
    const needle = quote.trim().replace(/[.\s]+$/, "").toLowerCase();
    if (needle.length < 8) continue;
    const start = lower.indexOf(needle);
    if (start >= 0) ranges.push([start, start + needle.length]);
  }
  if (!ranges.length) return text;
  ranges.sort((x, y) => x[0] - y[0]);
  const parts: ReactNode[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start < at) continue;
    parts.push(text.slice(at, start));
    parts.push(
      <mark key={start} className="rounded bg-amber-200/70 px-0.5 text-foreground dark:bg-amber-500/30">
        {text.slice(start, end)}
      </mark>,
    );
    at = end;
  }
  parts.push(text.slice(at));
  return parts;
}
