"use client";

import { useFormStatus } from "react-dom";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export function LoginButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" size="lg" className="bg-white text-sidebar hover:bg-white/90" disabled={pending}>
      {pending && <Loader2 className="h-4 w-4 animate-spin" />}
      {pending ? "Opening…" : "Log in to workspace"}
    </Button>
  );
}
