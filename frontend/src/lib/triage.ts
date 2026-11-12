import type { TriageMode } from "@/lib/api/client";

export const TRIAGE_MODES: { value: TriageMode; label: string; description: string }[] = [
  {
    value: "off",
    label: "Off",
    description: "New tickets wait for a person to choose their category.",
  },
  {
    value: "shadow",
    label: "Shadow",
    description:
      "The AI predicts each new ticket's category silently. Analytics compare its guesses with what people choose, before anything is applied.",
  },
  {
    value: "on",
    label: "On",
    description:
      "Categories the AI is confident about are set automatically (routing then uses them); the rest are shown as suggestions to accept.",
  },
];

/** "Nepali" for "ne" (in English), or the code itself if the browser doesn't know it. */
export function languageName(code: string | null | undefined): string | null {
  if (!code) return null;
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}
