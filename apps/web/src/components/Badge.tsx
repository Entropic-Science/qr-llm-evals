import type { PropsWithChildren } from "react";
export type BadgeTone = "quantum" | "control" | "positive" | "muted" | "warning";
export function Badge({ children, tone = "muted" }: PropsWithChildren<{ tone?: BadgeTone }>) { return <span className={`badge badge--${tone}`}>{children}</span>; }
