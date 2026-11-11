import type { Metadata } from "next";

import { AskKnowledgeBase } from "@/components/answers/ask-knowledge-base";
import { RequireStaff } from "@/components/guards";

export const metadata: Metadata = { title: "Ask the knowledge base" };

export default function Page() {
  return (
    <RequireStaff>
      <AskKnowledgeBase />
    </RequireStaff>
  );
}
