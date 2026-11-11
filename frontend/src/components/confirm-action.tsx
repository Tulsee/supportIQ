"use client";

import { Loader2Icon } from "lucide-react";
import { useState } from "react";
import type { ComponentProps, ReactNode } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

/** A trigger button that asks for confirmation before running a destructive action. */
export function ConfirmAction({
  children,
  triggerProps,
  title,
  description,
  confirmLabel,
  onConfirm,
}: {
  children: ReactNode;
  triggerProps?: ComponentProps<typeof Button>;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  /** Throw (or reject) to keep the dialog open, e.g. after showing an error toast. */
  onConfirm: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const confirm = async () => {
    setPending(true);
    try {
      await onConfirm();
      setOpen(false);
    } catch {
      // The caller reported the error; leave the dialog open.
    } finally {
      setPending(false);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger render={<Button {...triggerProps} />}>{children}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={pending} onClick={confirm}>
            {pending ? <Loader2Icon className="animate-spin" aria-hidden /> : null}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
