import { useEffect, useState, type ReactNode } from "react";

import { Sidebar } from "./sidebar";
import { TopBar } from "./topbar";

interface SessionExpiredEvent extends CustomEvent {
  detail: { reason_code: string };
}

/*
 * Shell — the partner-side console's outer chrome.
 *
 * Layout: left sidebar (collapsible), top bar, and the active
 * route in the main pane. Linear-inspired (per
 * docs/12-visual-builder.md §10.2.A row "Shell"); dark-first with
 * deep electric blue (#4F46E5) accent. Listens for the session-
 * expired event from useObsoleteTokenGuard() and renders a
 * dismiss-then-reload banner.
 */
export function Shell({ children }: { children: ReactNode }) {
  const [sessionExpired, setSessionExpired] = useState<string | null>(null);

  useEffect(() => {
    function handler(event: Event) {
      const reason = (event as SessionExpiredEvent).detail?.reason_code;
      setSessionExpired(reason ?? "CUSTOMER_USER_OAUTH_TOKEN_EXPIRED");
    }
    window.addEventListener("brickvision:session-expired", handler);
    return () =>
      window.removeEventListener("brickvision:session-expired", handler);
  }, []);

  return (
    <div className="flex h-full flex-col">
      {sessionExpired && <SessionExpiredBanner reasonCode={sessionExpired} />}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <div className="flex flex-1 flex-col">
          <TopBar />
          <main className="flex-1 overflow-auto bg-background">{children}</main>
        </div>
      </div>
    </div>
  );
}

function SessionExpiredBanner({ reasonCode }: { reasonCode: string }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between border-b border-destructive/40 bg-destructive/10 px-4 py-2 text-sm"
    >
      <div>
        <span className="font-medium">
          Your session has expired — please reload the console and try again.
        </span>
        <span className="ml-2 font-mono text-xs text-muted-foreground">
          ({reasonCode})
        </span>
      </div>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="rounded-md border border-destructive/40 px-2 py-1 text-xs font-medium hover:bg-destructive/20"
      >
        Reload
      </button>
    </div>
  );
}
