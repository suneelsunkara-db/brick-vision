import { Link, useLocation } from "@tanstack/react-router";
import {
  Activity,
  BookOpen,
  FlaskConical,
  GitBranch,
  Hammer,
  Library,
  Sparkles,
  Target,
  Workflow,
} from "lucide-react";
import { type ComponentType } from "react";

import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<{ className?: string }>;
}

const NAV: NavItem[] = [
  { to: "/usecases", label: "Opportunities", Icon: Target },
  { to: "/knowledge", label: "Capability Graph", Icon: Library },
  { to: "/migrations", label: "Migration Workflows", Icon: GitBranch },
  { to: "/workspace", label: "Workspace Context", Icon: Workflow },
  { to: "/skill-builder", label: "Skills", Icon: Hammer },
  { to: "/evaluation", label: "Evaluation", Icon: FlaskConical },
  { to: "/observability", label: "Observability", Icon: Activity },
];

export function Sidebar() {
  const { pathname } = useLocation();

  return (
    <aside className="hidden h-full w-56 shrink-0 flex-col border-r border-border bg-card md:flex">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/15">
          <Sparkles className="h-4 w-4 text-primary" aria-hidden="true" />
        </div>
        <span className="font-semibold tracking-tight">BrickVision</span>
      </div>

      <nav className="flex-1 overflow-y-auto p-2">
        <ul className="space-y-1">
          {NAV.map(({ to, label, Icon }) => {
            const active = pathname === to || pathname.startsWith(`${to}/`);
            return (
              <li key={to}>
                <Link
                  to={to}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                    active
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  <span>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-border p-3 text-xs text-muted-foreground">
        <a
          href="https://github.com/databricks/brickvision"
          className="inline-flex items-center gap-1 hover:text-foreground"
        >
          <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
          Docs
        </a>
      </div>
    </aside>
  );
}
