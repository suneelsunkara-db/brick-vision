import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";

/*
 * TopBar — current build / current workspace / OBO user identity
 * stub. The OBO user is rendered server-side into a meta tag on
 * index.html and read here; the React client never sees the raw
 * token.
 */
export function TopBar() {
  // Read the user identity injected by the FastAPI sidecar's HTML
  // template (see apps/console-api/src/console_api/main.py).
  const userEmail =
    document
      .querySelector('meta[name="bv-user-email"]')
      ?.getAttribute("content") ?? "(unauthenticated)";

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card/50 px-4">
      <Button
        variant="outline"
        size="sm"
        className="gap-2 text-muted-foreground"
        onClick={() => {
          window.dispatchEvent(new CustomEvent("brickvision:open-cmdk"));
        }}
        aria-keyshortcuts="Meta+K"
      >
        <Search className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Search Top-Orders, Meta-Skills, Extensions…</span>
        <kbd className="ml-2 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          ⌘K
        </kbd>
      </Button>
      <div className="flex-1" />
      <div
        className="text-xs text-muted-foreground"
        title="UC OBO X-Forwarded-Access-Token user identity"
      >
        {userEmail}
      </div>
    </header>
  );
}
