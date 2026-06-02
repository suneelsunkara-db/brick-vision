import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Database,
  type LucideIcon,
  ServerCog,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/observability")({
  component: ObservabilityPage,
});

interface ObservabilityOverview {
  status: string;
  scope: string;
  summary: {
    active_snapshot_id: string | null;
    indexer_state: string;
    freshness_days: number | null;
    is_stale: boolean;
    refresh_run_count: number;
    llm_usage_instrumented: boolean;
    budget_usage_instrumented: boolean;
  };
  infra: {
    capability_graph: Record<string, unknown>;
    lakebase: {
      status: string;
      project_id: string;
      branch: string;
      database: string;
    };
    vector_search: {
      endpoint: string;
      index_name: string;
    };
    sql_warehouse: {
      warehouse_id: string;
    };
  };
  jobs: {
    refresh_history: Array<Record<string, unknown>>;
    latest_refresh: Record<string, unknown> | null;
  };
  models: {
    configured: Array<{
      role: string;
      env_var: string;
      endpoint: string;
      status: string;
    }>;
    observed_usage: {
      status: string;
      source?: string;
      call_count: number | null;
      input_tokens: number | null;
      output_tokens: number | null;
      token_usage_quantity?: number | null;
      usage_quantity?: number | null;
      endpoint_count?: number | null;
      estimated_cost_usd: number | null;
      message?: string;
    };
    attribution: SystemSection;
    gaps: string[];
  };
  databricks_system: {
    billing: SystemSection;
    model_serving: SystemSection & {
      record_count?: number;
      usage_quantity?: number;
      token_usage_quantity?: number;
      endpoint_count?: number;
    };
    jobs: SystemSection;
    queries: SystemSection;
    audit: SystemSection;
    lookback_days: number;
  };
  usage: {
    budget_namespaces: Array<{ namespace: string; ledger_table: string }>;
    proof_counts: Record<string, number>;
    tables: Record<
      string,
      {
        exists: boolean;
        row_count: number | null;
        status: string;
        message?: string;
      }
    >;
    gaps: string[];
  };
  next_action: string;
}

interface SystemSection {
  status: string;
  source?: string;
  message?: string;
  rows: Array<Record<string, unknown>>;
  lookback_days?: number;
}

interface DetailPayload {
  status: string;
  source: string;
  message?: string;
  rows: Array<Record<string, unknown>>;
  days?: number;
  hours?: number;
  endpoint_count?: number;
  record_count?: number;
  token_usage_quantity?: number;
  usage_quantity?: number;
  run_count?: number;
  failure_count?: number;
  query_count?: number;
  avg_duration_ms?: number;
  warehouse_id?: string;
}

function ObservabilityPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["observability-overview"],
    queryFn: () =>
      fetchJson<ObservabilityOverview>("/api/knowledge/observability"),
    staleTime: 30_000,
  });
  const modelServingDetail = useQuery({
    queryKey: ["observability-model-serving-detail"],
    queryFn: () =>
      fetchJson<DetailPayload>("/api/knowledge/observability/model-serving", {
        query: { days: 7 },
      }),
    staleTime: 60_000,
  });
  const jobsDetail = useQuery({
    queryKey: ["observability-jobs-detail"],
    queryFn: () =>
      fetchJson<DetailPayload>("/api/knowledge/observability/jobs", {
        query: { days: 7 },
      }),
    staleTime: 60_000,
  });
  const sqlDetail = useQuery({
    queryKey: ["observability-sql-detail"],
    queryFn: () =>
      fetchJson<DetailPayload>("/api/knowledge/observability/sql", {
        query: { hours: 24 },
      }),
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-6 p-6">
        <PanelSkeleton />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Observability</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-destructive">
              Could not load BrickVision observability data.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const modelServing = modelServingDetail.data;
  const jobs = jobsDetail.data;
  const sql = sqlDetail.data;
  const healthIssues = [
    data.summary.is_stale ? "Capability graph refresh is stale." : null,
    modelServing?.status && modelServing.status !== "ready"
      ? "LLM/model-serving usage is unavailable."
      : null,
    jobs?.failure_count ? `${jobs.failure_count} BrickVision job failures found.` : null,
    sql?.failure_count ? `${sql.failure_count} SQL query failures found.` : null,
  ].filter(Boolean) as string[];
  const overallHealthy = healthIssues.length === 0;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          BrickVision Health
        </h1>
        <p className="text-sm text-muted-foreground">
          A guided view of the platform signals that matter: refresh health,
          LLM usage, jobs, SQL warehouse activity, and Databricks system-table
          coverage.
        </p>
      </header>

      <Card className={cn(overallHealthy ? "border-emerald-500/30 bg-emerald-500/5" : "border-amber-500/30 bg-amber-500/5")}>
        <CardHeader>
          <CardTitle className="flex items-center justify-between gap-3">
            <span>{overallHealthy ? "Everything looks healthy" : "Needs attention"}</span>
            <StatusPill ok={overallHealthy}>{overallHealthy ? "healthy" : "review"}</StatusPill>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {overallHealthy ? (
            <p className="text-sm text-muted-foreground">
              BrickVision has a current capability graph, visible LLM usage,
              successful recent jobs, and healthy SQL query history.
            </p>
          ) : (
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {healthIssues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard
          title="Capability Graph"
          value={data.summary.indexer_state}
          detail={
            data.summary.active_snapshot_id
              ? `Snapshot ${data.summary.active_snapshot_id}`
              : "No active snapshot"
          }
          ok={!data.summary.is_stale && data.summary.indexer_state !== "missing"}
          Icon={Activity}
        />
        <MetricCard
          title="LLM Tokens"
          value={formatMetric(modelServing?.token_usage_quantity)}
          detail={
            modelServingDetail.isLoading
              ? "Loading 7-day usage"
              : `${formatMetric(modelServing?.record_count)} billing records`
          }
          ok={modelServing?.status === "ready"}
          Icon={BrainCircuit}
        />
        <MetricCard
          title="Job Failures"
          value={formatMetric(jobs?.failure_count)}
          detail={
            jobsDetail.isLoading
              ? "Loading recent runs"
              : `${formatMetric(jobs?.run_count)} BrickVision runs in 7 days`
          }
          ok={(jobs?.failure_count ?? 0) === 0 && jobs?.status === "ready"}
          Icon={Clock3}
        />
        <MetricCard
          title="SQL Latency"
          value={formatDurationMs(sql?.avg_duration_ms)}
          detail={
            sqlDetail.isLoading
              ? "Loading query history"
              : `${formatMetric(sql?.query_count)} queries in 24 hours`
          }
          ok={(sql?.failure_count ?? 0) === 0 && sql?.status === "ready"}
          Icon={Database}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard
          title="Refresh Runs"
          value={String(data.summary.refresh_run_count)}
          detail={
            data.summary.freshness_days === null
              ? "Freshness unknown"
              : `${data.summary.freshness_days} days old`
          }
          ok={data.summary.refresh_run_count > 0}
          Icon={Clock3}
        />
        <MetricCard
          title="Usage Source"
          value={data.summary.llm_usage_instrumented ? "ready" : "missing"}
          detail={
            data.summary.llm_usage_instrumented
              ? data.models.observed_usage.source ?? "System usage available"
              : "System usage unavailable"
          }
          ok={data.summary.llm_usage_instrumented}
          Icon={BrainCircuit}
        />
        <MetricCard
          title="Billing Source"
          value={data.summary.budget_usage_instrumented ? "ready" : "missing"}
          detail={
            data.summary.budget_usage_instrumented
              ? data.databricks_system.billing.source ?? "Usage source available"
              : "Usage source missing"
          }
          ok={data.summary.budget_usage_instrumented}
          Icon={Database}
        />
      </div>

      <DrilldownCards
        modelServing={modelServingDetail}
        jobs={jobsDetail}
        sql={sqlDetail}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <ModelsCard data={data} />
        <InfraCard data={data} />
      </div>

      <AttributionCard data={data} />

      <SystemTablesCard data={data} />

      <Card>
        <CardHeader>
          <CardTitle>Current Next Action</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{data.next_action}</p>
        </CardContent>
      </Card>
    </div>
  );
}

function DrilldownCards({
  modelServing,
  jobs,
  sql,
}: {
  modelServing: UseQueryResult<DetailPayload>;
  jobs: UseQueryResult<DetailPayload>;
  sql: UseQueryResult<DetailPayload>;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <DetailCard
        title="Are LLMs Being Used?"
        description="7-day model-serving usage for the configured BrickVision endpoints."
        query={modelServing}
        summaryFields={["endpoint_count", "record_count", "token_usage_quantity"]}
        rowFields={["endpoint_name", "usage_type", "usage_quantity"]}
      />
      <DetailCard
        title="Did BrickVision Jobs Fail?"
        description="Recent runs for BrickVision-managed Databricks Jobs only."
        query={jobs}
        summaryFields={["run_count", "failure_count", "days"]}
        rowFields={["job_name", "result_state", "run_duration_seconds"]}
      />
      <DetailCard
        title="Is SQL Healthy?"
        description="Recent query history for the configured BrickVision SQL warehouse."
        query={sql}
        summaryFields={["query_count", "failure_count", "avg_duration_ms"]}
        rowFields={["execution_status", "statement_type", "total_duration_ms"]}
      />
    </div>
  );
}

function DetailCard({
  title,
  description,
  query,
  summaryFields,
  rowFields,
}: {
  title: string;
  description: string;
  query: UseQueryResult<DetailPayload>;
  summaryFields: string[];
  rowFields: string[];
}) {
  const data = query.data;
  const detailRecord = data as Record<string, unknown> | undefined;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">{title}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          </div>
          <StatusPill ok={data?.status === "ready"}>
            {query.isLoading ? "loading" : data?.status ?? "unknown"}
          </StatusPill>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="truncate text-xs text-muted-foreground">
          Source: {data?.source ?? "Loading filtered Databricks data..."}
        </p>
        {query.error ? (
          <p className="text-xs text-destructive">Could not load detail.</p>
        ) : null}
        {data?.message ? (
          <p className="text-xs text-muted-foreground">{data.message}</p>
        ) : null}
        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
          {summaryFields.map((field) => (
            <span key={field}>
              <span className="text-foreground">{prettyLabel(field)}:</span>{" "}
              {formatFieldValue(field, detailRecord?.[field])}
            </span>
          ))}
        </div>
        <div className="space-y-2">
          {(data?.rows ?? []).slice(0, 5).map((row, index) => (
            <div
              key={`${title}-${index}`}
              className="rounded bg-muted/40 p-2 text-xs text-muted-foreground"
            >
              {rowFields.map((field) => (
                <span key={field} className="mr-3">
                  <span className="text-foreground">{prettyLabel(field)}:</span>{" "}
                  {formatFieldValue(field, row[field])}
                </span>
              ))}
            </div>
          ))}
          {data && data.rows.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No matching rows for the current filter window.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function MetricCard({
  title,
  value,
  detail,
  ok,
  Icon,
}: {
  title: string;
  value: string;
  detail: string;
  ok: boolean;
  Icon: LucideIcon;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon
          className={cn("h-4 w-4", ok ? "text-emerald-500" : "text-amber-500")}
          aria-hidden="true"
        />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
        <p className="mt-1 truncate text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

function InfraCard({ data }: { data: ObservabilityOverview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ServerCog className="h-5 w-5" aria-hidden="true" />
          Infra
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Fact label="Lakebase" value={data.infra.lakebase.status} />
        <Fact label="Lakebase branch" value={data.infra.lakebase.branch || "-"} />
        <Fact label="SQL warehouse" value={data.infra.sql_warehouse.warehouse_id || "-"} />
        <Fact label="Vector endpoint" value={data.infra.vector_search.endpoint || "-"} />
        <Fact label="Entity index" value={data.infra.vector_search.index_name || "-"} />
      </CardContent>
    </Card>
  );
}

function ModelsCard({ data }: { data: ObservabilityOverview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BrainCircuit className="h-5 w-5" aria-hidden="true" />
          LLMs & Tokens
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-md border border-border p-3">
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="font-medium">Observed usage</span>
            <StatusPill ok={data.models.observed_usage.status === "ready"}>
              {data.models.observed_usage.status}
            </StatusPill>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <span>Source: {data.models.observed_usage.source ?? "-"}</span>
            <span>Records: {valueOrDash(data.models.observed_usage.call_count)}</span>
            <span>Input tokens: {valueOrDash(data.models.observed_usage.input_tokens)}</span>
            <span>Output tokens: {valueOrDash(data.models.observed_usage.output_tokens)}</span>
            <span>Token usage: {valueOrDash(data.models.observed_usage.token_usage_quantity)}</span>
            <span>Usage quantity: {valueOrDash(data.models.observed_usage.usage_quantity)}</span>
            <span>Endpoints: {valueOrDash(data.models.observed_usage.endpoint_count)}</span>
          </div>
          {data.models.observed_usage.message ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {data.models.observed_usage.message}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <h3 className="text-sm font-medium">Configured model roles</h3>
          {data.models.configured.map((model) => (
            <div
              key={model.env_var}
              className="rounded-md border border-border p-3 text-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium">{model.role}</span>
                <StatusPill ok={model.status === "configured"}>
                  {model.status}
                </StatusPill>
              </div>
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                {model.endpoint || model.env_var}
              </p>
            </div>
          ))}
        </div>

        {data.models.gaps.length > 0 ? (
          <GapList gaps={data.models.gaps} />
        ) : null}
      </CardContent>
    </Card>
  );
}

function AttributionCard({ data }: { data: ObservabilityOverview }) {
  const attribution = data.models.attribution;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <span>Which BrickVision Feature Used The Model?</span>
          <StatusPill ok={attribution.status === "ready"}>
            {attribution.status}
          </StatusPill>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Databricks system tables show billable usage. This ledger adds
          BrickVision attribution: feature, model role, endpoint, latency, and
          success/failure.
        </p>
        <div className="space-y-2">
          {attribution.rows.slice(0, 8).map((row, index) => (
            <div
              key={`attribution-${index}`}
              className="grid gap-2 rounded-md border border-border p-3 text-sm md:grid-cols-5"
            >
              <div>
                <div className="text-xs text-muted-foreground">Feature</div>
                <div className="font-medium">{String(row.feature ?? "-")}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Role</div>
                <div>{String(row.model_role ?? "-")}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Endpoint</div>
                <div className="truncate font-mono text-xs">
                  {String(row.endpoint ?? "-")}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Calls</div>
                <div>{formatMetric(row.invocation_count as number)}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Avg latency</div>
                <div>{formatDurationMs(row.avg_latency_ms as number)}</div>
              </div>
            </div>
          ))}
          {attribution.rows.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No BrickVision-attributed model calls have been recorded yet.
              Run Knowledge search or ask a Knowledge question to populate this
              section.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function SystemTablesCard({ data }: { data: ObservabilityOverview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-5 w-5" aria-hidden="true" />
          Advanced: Databricks Sources
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <SystemSectionPanel
          title="Billing by Product"
          section={data.databricks_system.billing}
          fields={["billing_origin_product", "usage_type", "usage_quantity"]}
        />
        <SystemSectionPanel
          title="Model Serving"
          section={data.databricks_system.model_serving}
          fields={["endpoint_name", "usage_type", "usage_quantity"]}
        />
        <SystemSectionPanel
          title="Lakeflow Jobs"
          section={data.databricks_system.jobs}
          fields={["result_state", "run_count", "duration_seconds"]}
        />
        <SystemSectionPanel
          title="SQL Query History"
          section={data.databricks_system.queries}
          fields={["execution_status", "query_count", "avg_duration_ms"]}
        />
        <SystemSectionPanel
          title="Audit Events"
          section={data.databricks_system.audit}
          fields={["service_name", "action_name", "event_count"]}
        />
      </CardContent>
    </Card>
  );
}

function SystemSectionPanel({
  title,
  section,
  fields,
}: {
  title: string;
  section: SystemSection;
  fields: string[];
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="text-xs text-muted-foreground">{section.source ?? "-"}</p>
        </div>
        <StatusPill ok={section.status === "ready"}>{section.status}</StatusPill>
      </div>
      <div className="mt-3 space-y-2">
        {section.rows.slice(0, 5).map((row, index) => (
          <div
            key={`${title}-${index}`}
            className="rounded bg-muted/40 p-2 text-xs text-muted-foreground"
          >
            {fields.map((field) => (
              <span key={field} className="mr-3">
                <span className="text-foreground">{prettyLabel(field)}:</span>{" "}
                {formatFieldValue(field, row[field])}
              </span>
            ))}
          </div>
        ))}
        {section.rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            {section.message ?? "No rows available."}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[65%] truncate text-right font-mono text-xs">
        {value}
      </span>
    </div>
  );
}

function StatusPill({
  ok,
  children,
}: {
  ok: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs",
        ok ? "bg-emerald-500/10 text-emerald-600" : "bg-amber-500/10 text-amber-600",
      )}
    >
      {ok ? (
        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
      ) : (
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
      )}
      {children}
    </span>
  );
}

function GapList({ gaps }: { gaps: string[] }) {
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
      <h3 className="text-sm font-medium text-amber-700">Instrumentation gaps</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
        {gaps.map((gap) => (
          <li key={gap}>{gap}</li>
        ))}
      </ul>
    </div>
  );
}

function valueOrDash(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function formatMetric(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(1)}M`;
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(1)}K`;
  if (Number.isInteger(numeric)) return String(numeric);
  return numeric.toFixed(2);
}

function formatDurationMs(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "-";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (numeric >= 1000) return `${(numeric / 1000).toFixed(1)}s`;
  return `${Math.round(numeric)}ms`;
}

function formatFieldValue(field: string, value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (
    field.endsWith("_ms") ||
    field === "avg_duration_ms" ||
    field === "total_duration_ms"
  ) {
    return formatDurationMs(value as number | string);
  }
  if (field.endsWith("_seconds") || field === "duration_seconds") {
    const numeric = typeof value === "number" ? value : Number(value);
    return Number.isFinite(numeric) ? `${Math.round(numeric)}s` : String(value);
  }
  if (
    field.includes("count") ||
    field.includes("quantity") ||
    field === "days" ||
    field === "hours"
  ) {
    return formatMetric(value as number | string);
  }
  return String(value);
}

function prettyLabel(field: string) {
  const labels: Record<string, string> = {
    action_name: "Action",
    avg_duration_ms: "Avg latency",
    billing_origin_product: "Product",
    days: "Days",
    duration_seconds: "Duration",
    endpoint_count: "Endpoints",
    endpoint_name: "Endpoint",
    event_count: "Events",
    execution_status: "Status",
    failure_count: "Failures",
    job_name: "Job",
    query_count: "Queries",
    record_count: "Records",
    result_state: "Result",
    run_count: "Runs",
    run_duration_seconds: "Duration",
    service_name: "Service",
    sku_name: "SKU",
    statement_type: "Type",
    token_usage_quantity: "Tokens",
    total_duration_ms: "Latency",
    usage_quantity: "Usage",
    usage_type: "Usage type",
  };
  return labels[field] ?? field.replaceAll("_", " ");
}
