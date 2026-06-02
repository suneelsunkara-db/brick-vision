import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./api";

/*
 * useObsoleteTokenGuard — listens for FastAPI 401 responses
 * carrying `WWW-Authenticate: Bearer error="invalid_token"`
 * (per docs/12-visual-builder.md §10.7.7.C) and surfaces the
 * canonical session-expired UX.
 *
 * The hook subscribes to the TanStack Query cache's mutation
 * lifecycle. On a 401 ApiError, it:
 *   - rolls back any in-flight optimistic mutation by invalidating
 *     the affected queries (the per-mutation `onError` should also
 *     restore the snapshot via queryClient.setQueryData),
 *   - dispatches a global event the Shell layout listens to in
 *     order to render the canonical "Your session has expired"
 *     banner with a Reload action.
 *
 * EndCustomerConsoleConformance() ts-morph TSX-AST lint
 * (docs/17-eval-framework.md §13.3 check (g)) verifies every
 * action-mutation handler calls this hook.
 */
export function useObsoleteTokenGuard() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const cache = queryClient.getMutationCache();
    const unsubscribe = cache.subscribe((event) => {
      if (event.type !== "updated") return;
      const error = event.mutation.state.error;
      if (error instanceof ApiError && error.status === 401) {
        queryClient.invalidateQueries();
        window.dispatchEvent(
          new CustomEvent("brickvision:session-expired", {
            detail: { reason_code: "CUSTOMER_USER_OAUTH_TOKEN_EXPIRED" },
          }),
        );
      }
    });
    return unsubscribe;
  }, [queryClient]);
}
