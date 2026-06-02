import "./styles/app.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";

import { routeTree } from "./routeTree.gen";

// TanStack Query client. Defaults are tuned for the console's
// read-mostly workload: 30 s staleTime aligns with the 30-60 s
// panel refresh cadence per docs/12-visual-builder.md §10.7.7.A.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Never retry 4xx (auth, validation) — let the user see them.
        const maybeStatus = (error as { status?: number } | null)?.status;
        if (maybeStatus !== undefined && maybeStatus >= 400 && maybeStatus < 500) {
          return false;
        }
        return failureCount < 2;
      },
    },
  },
});

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  context: { queryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("BrickVision Console: #root element missing");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
