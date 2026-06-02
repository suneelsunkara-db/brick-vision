/*
 * Thin fetch wrapper for the FastAPI sidecar at apps/console-api.
 *
 * UC OBO is server-side: the sidecar reads the
 * X-Forwarded-Access-Token header from each incoming request and
 * uses it to construct a `WorkspaceClient(token=...)`. The browser
 * never sees the token.
 *
 * On 401 with `WWW-Authenticate: Bearer error="invalid_token"`,
 * the `useObsoleteTokenGuard()` hook (lib/auth.ts) intercepts and
 * surfaces the canonical session-expired UX.
 */

export class ApiError extends Error {
  status: number;
  reason_code?: string;
  details?: unknown;

  constructor(
    status: number,
    message: string,
    reason_code?: string,
    details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.reason_code = reason_code;
    this.details = details;
  }
}

const baseUrl = ""; // same-origin; the SPA + FastAPI ship as one App.

async function parseError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = await response.text();
  }
  const reason =
    typeof body === "object" && body !== null && "reason_code" in body
      ? String((body as { reason_code: unknown }).reason_code)
      : undefined;
  const message =
    typeof body === "object" && body !== null && "message" in body
      ? String((body as { message: unknown }).message)
      : `HTTP ${response.status} ${response.statusText}`;
  return new ApiError(response.status, message, reason, body);
}

export interface FetchJsonOptions extends RequestInit {
  query?: Record<string, string | number | boolean | undefined>;
}

export async function fetchJson<T>(
  path: string,
  options: FetchJsonOptions = {},
): Promise<T> {
  const { query, headers, ...rest } = options;

  const url = new URL(`${baseUrl}${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined) url.searchParams.set(k, String(v));
    }
  }

  const response = await fetch(url.toString(), {
    credentials: "same-origin",
    ...rest,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...headers,
    },
  });

  if (!response.ok) {
    throw await parseError(response);
  }

  // Some endpoints (DELETE) return 204 no-content.
  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
