import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";

import { CommandPalette } from "@/components/layout/command-palette";
import { Shell } from "@/components/layout/shell";
import { useObsoleteTokenGuard } from "@/lib/auth";

interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
});

function RootComponent() {
  useObsoleteTokenGuard();
  return (
    <Shell>
      <CommandPalette />
      <Outlet />
    </Shell>
  );
}
