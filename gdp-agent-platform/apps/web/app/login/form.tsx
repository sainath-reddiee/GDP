"use client";

import { useFormState, useFormStatus } from "react-dom";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { login } from "./actions";

function Submit() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="mt-4 w-full" disabled={pending}>
      {pending && <Loader2 className="h-4 w-4 animate-spin" />}
      {pending ? "Signing in…" : "Sign in"}
    </Button>
  );
}

export function LoginForm() {
  const [state, action] = useFormState(login, null);
  return (
    <form action={action}>
      <Label htmlFor="user">Snowflake user</Label>
      <Input id="user" name="user" required autoComplete="username" placeholder="Your Snowflake user name" />
      <Label htmlFor="token">Access token</Label>
      <Input id="token" name="token" type="password" required autoComplete="off" placeholder="Used only for this session" />
      <p className="mt-2 text-xs text-muted-foreground">Your token is not saved. Sign out ends this session.</p>
      {state && !state.ok && <p className="mt-3 text-sm text-destructive">{state.error}</p>}
      <Submit />
    </form>
  );
}
