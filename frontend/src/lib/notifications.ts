import type { NotificationDelivery, NotificationKind } from "@/lib/api/client";

export const KIND_LABELS: Record<NotificationKind, { label: string; description: string }> = {
  ticket_assigned: {
    label: "Assigned to you",
    description: "Someone else assigns a ticket to you.",
  },
  customer_replied: {
    label: "Customer replies",
    description: "The customer replies on a ticket assigned to you.",
  },
  ai_escalated: {
    label: "AI escalations",
    description: "The AI can't draft a supported reply on a ticket assigned to you.",
  },
  ticket_closed: {
    label: "Closed by the customer",
    description: "The customer closes a ticket assigned to you.",
  },
  agent_replied: {
    label: "Replies from support",
    description: "Support replies to one of your requests.",
  },
};

export const DELIVERY_LABELS: Record<NotificationDelivery, string> = {
  off: "Off",
  in_app: "In the app",
  email: "In the app and by email",
};

export const DELIVERIES: NotificationDelivery[] = ["in_app", "email", "off"];

export const AUDIT_ENTITY_LABELS = {
  organization: "Organization",
  member: "Members",
  invitation: "Invitations",
  document: "Documents",
} as const;

export type AuditEntityType = keyof typeof AUDIT_ENTITY_LABELS;
