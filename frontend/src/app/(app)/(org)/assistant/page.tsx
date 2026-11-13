import type { Metadata } from "next";

import { CustomerAssistant } from "@/components/assistant/customer-assistant";

export const metadata: Metadata = { title: "Ask a question" };

export default function Page() {
  return <CustomerAssistant />;
}
