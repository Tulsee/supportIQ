import type { Metadata } from "next";

import { CustomersList } from "@/components/customers/customers-list";
import { RequireStaff } from "@/components/guards";

export const metadata: Metadata = { title: "Customers" };

export default function Page() {
  return (
    <RequireStaff>
      <CustomersList />
    </RequireStaff>
  );
}
