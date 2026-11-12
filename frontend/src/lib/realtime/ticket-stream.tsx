"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { session } from "@/lib/auth/session";
import { useActiveOrganization } from "@/lib/org/org-provider";
import { queryKeys } from "@/lib/query-keys";

type Notification = { type: "ready" } | { type: string; ticket_id: string };

// Close codes from the API (see backend app/api/v1/endpoints/realtime.py).
const BAD_REQUEST = 4400;
const UNAUTHORIZED = 4401;
const NOT_FOUND = 4404;
const MAX_BACKOFF_MS = 30_000;

/**
 * Where the realtime endpoint lives. By default the page's own origin: the dev
 * server proxies /api (WebSocket upgrades included), and in production the same
 * reverse proxy should route it. NEXT_PUBLIC_WS_URL overrides that, e.g.
 * wss://api.example.com when the API is served from another host.
 */
function streamUrl(path: string): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  const base =
    configured ??
    `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return `${base.replace(/\/$/, "")}${path}`;
}

/**
 * Live ticket updates for the active organization. Each notification only says
 * which ticket changed; the affected queries refetch through the REST API (so
 * every authorization rule still applies). Reconnects with backoff, and after a
 * reconnect refetches everything, since notifications may have been missed.
 */
export function useTicketStream(): void {
  const organization = useActiveOrganization();
  const queryClient = useQueryClient();
  const orgId = organization.id;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    let attempts = 0;
    let everReady = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const refetchTickets = () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.tickets(orgId) });

    const scheduleReconnect = (delayMs: number) => {
      if (!stopped) retryTimer = setTimeout(connect, delayMs);
    };

    async function connect(): Promise<void> {
      const token = await session.getAccessToken(); // refreshes if about to expire
      if (stopped || !token) return; // signed out: the auth provider takes over

      const ws = new WebSocket(streamUrl("/api/v1/ws/tickets"));
      socket = ws;
      ws.onopen = () =>
        ws.send(JSON.stringify({ type: "auth", token, organization_id: orgId }));
      ws.onmessage = (event) => {
        const message = JSON.parse(event.data as string) as Notification;
        if (message.type === "ready") {
          if (everReady) void refetchTickets(); // catch up on anything missed
          everReady = true;
          attempts = 0;
          return;
        }
        if (message.type === "notification.created") {
          void queryClient.invalidateQueries({
            queryKey: queryKeys.notifications(orgId),
            refetchType: "all",
          });
          return;
        }
        void refetchTickets();
        if (message.type === "ticket.created") {
          void queryClient.invalidateQueries({ queryKey: queryKeys.customers(orgId) });
        }
      };
      ws.onclose = (event) => {
        socket = null;
        if (stopped || event.code === BAD_REQUEST || event.code === NOT_FOUND) return;
        attempts += 1;
        // An expired token reconnects at once (with a fresh one); repeated
        // failures of any kind back off exponentially, with jitter.
        const immediate = event.code === UNAUTHORIZED && attempts === 1;
        const backoff = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (attempts - 1));
        scheduleReconnect(immediate ? 0 : backoff * (0.5 + Math.random() / 2));
      };
    }

    void connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      socket?.close(1000);
    };
  }, [orgId, queryClient]);
}

/** Mount once inside an organization's pages. Renders nothing. */
export function TicketLiveUpdates() {
  useTicketStream();
  return null;
}
