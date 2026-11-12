"use client";

import { PlusIcon, XIcon } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { SkillLevel } from "@/lib/api/client";

export const LEVEL_LABELS: Record<string, string> = {
  "1": "1 · basic",
  "2": "2",
  "3": "3 · solid",
  "4": "4",
  "5": "5 · expert",
};

/** A skill slug as the API stores it: lower case, words joined by - or _. */
export function normalizeSkill(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, "-");
}

export function isValidSkill(text: string): boolean {
  return /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/.test(text) && text.length >= 2 && text.length <= 50;
}

/** Edit a list of skills with levels. `suggestions` are offered while typing. */
export function SkillsEditor({
  value,
  onChange,
  suggestions = [],
  label,
  addLabel = "Add skill",
}: {
  value: SkillLevel[];
  onChange: (skills: SkillLevel[]) => void;
  suggestions?: string[];
  label: string;
  addLabel?: string;
}) {
  const uid = useId();
  const listId = `${uid}-skills`;
  const update = (index: number, change: Partial<SkillLevel>) =>
    onChange(value.map((skill, i) => (i === index ? { ...skill, ...change } : skill)));

  return (
    <div className="space-y-2" role="group" aria-label={label}>
      <datalist id={listId}>
        {suggestions.map((skill) => (
          <option key={skill} value={skill} />
        ))}
      </datalist>
      {value.map((skill, index) => {
        const invalid = skill.skill !== "" && !isValidSkill(skill.skill);
        return (
          <div key={index} className="flex items-center gap-2">
            <Input
              aria-label={`Skill ${index + 1}`}
              aria-invalid={invalid}
              list={listId}
              value={skill.skill}
              placeholder="e.g. refunds"
              className="font-mono"
              maxLength={50}
              onChange={(event) => update(index, { skill: normalizeSkill(event.target.value) })}
            />
            <Select
              items={LEVEL_LABELS}
              value={String(skill.level)}
              onValueChange={(level) => level && update(index, { level: Number(level) })}
            >
              <SelectTrigger aria-label={`Level of skill ${index + 1}`} className="w-32 shrink-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(LEVEL_LABELS).map(([level, text]) => (
                  <SelectItem key={level} value={level}>
                    {text}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={`Remove skill ${skill.skill || index + 1}`}
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              <XIcon aria-hidden />
            </Button>
          </div>
        );
      })}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...value, { skill: "", level: 3 }])}
      >
        <PlusIcon aria-hidden />
        {addLabel}
      </Button>
    </div>
  );
}

/** Skills ready to send: complete, valid and each listed once. Null if any isn't. */
export function cleanSkills(skills: SkillLevel[]): SkillLevel[] | null {
  const filled = skills.filter((s) => s.skill !== "");
  if (filled.some((s) => !isValidSkill(s.skill))) return null;
  if (new Set(filled.map((s) => s.skill)).size !== filled.length) return null;
  return filled;
}
