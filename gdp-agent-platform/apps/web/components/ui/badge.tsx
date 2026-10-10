import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", {
  variants: {
    variant: {
      default: "border-primary/15 bg-primary/10 text-primary",
      secondary: "border-transparent bg-secondary text-secondary-foreground",
      success: "border-success/20 bg-success/10 text-success",
      warning: "border-warning/20 bg-warning/10 text-warning",
      destructive: "border-destructive/20 bg-destructive/10 text-destructive",
      outline: "border-border bg-card text-foreground/80",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
