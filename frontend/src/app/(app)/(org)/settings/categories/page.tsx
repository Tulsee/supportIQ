import type { Metadata } from "next";

import { RequireAdmin } from "@/components/guards";
import { CategoriesPage } from "@/components/settings/categories-page";

export const metadata: Metadata = { title: "Ticket categories" };

export default function SettingsCategoriesPage() {
  return (
    <RequireAdmin>
      <CategoriesPage />
    </RequireAdmin>
  );
}
