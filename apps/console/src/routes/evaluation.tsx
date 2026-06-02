import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Database,
  HelpCircle,
  type LucideIcon,
  XCircle,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/evaluation")({
  component: EvaluationPage,
});

interface EvaluationOverview {
  status: string;
  scope: string;
  links: {
    databricks_host: string;
    mlflow_experiment_id: string;
    mlflow_experiment_url: string;
  };
  summary: {
    dataset_count: number;
    record_count: number;
    workflow_count: number;
    mlflow_experiment_id: string;
    registry_status: string;
    recent_event_count: number;
    live_trace_count_24h: number;
    latest_scorer_run_count: number;
    latest_scorer_pass_count: number;
    latest_scorer_fail_count: number;
    overall_status: EvaluationCategoryStatus;
    category_pass_count: number;
    category_warning_count: number;
    category_fail_count: number;
    category_not_scored_count: number;
  };
  contract: {
    storage: string;
    required_fields: string[];
    optional_fields: string[];
    expectation_reserved_keys: string[];
    supported_sources: string[];
    max_records_per_dataset: number;
    next_action: string;
  };
  workflows: EvaluationWorkflow[];
  evaluation_categories: EvaluationCategory[];
  datasets: EvaluationDataset[];
  live_quality: LiveQualityRow[];
  recent_events: EvaluationEvent[];
  latest_scorer_runs: EvaluationScorerRun[];
  registry: {
    status: string;
    table: string;
    message?: string;
  };
}

type EvaluationCategoryStatus = "passed" | "warning" | "failed" | "not_scored";

interface EvaluationCategory {
  id: string;
  title: string;
  workflow: string;
  scope: string;
  status: EvaluationCategoryStatus;
  numerator: number;
  denominator: number;
  evidence: string;
  reason_summary: string;
  reason_details: string[];
  next_action: string;
  created_at_ms: number;
  mlflow_run_id: string;
  mlflow_trace_id: string;
}

interface EvaluationWorkflow {
  workflow: string;
  title: string;
  dataset_count: number;
  record_count: number;
  status: string;
  latest_scorer_run?: EvaluationScorerRun | null;
}

interface EvaluationScorerRun {
  workflow: string;
  status: string;
  subject_id: string;
  mlflow_run_id: string;
  mlflow_trace_id: string;
  mlflow_dataset_name: string;
  metrics: Record<string, unknown>;
  quality_gates: Record<string, unknown>;
  scorer_results: EvaluationScorerResult[];
  reason_codes: string[];
  created_at_ms: number;
}

interface EvaluationScorerResult {
  name: string;
  label: string;
  value: number | null;
  threshold: number | null;
  status: string;
  business_label: string;
}

interface EvaluationDataset {
  dataset_id: string;
  name: string;
  workflow: string;
  uc_table_name: string;
  mlflow_experiment_id: string;
  description: string;
  quality_gates: Record<string, unknown>;
  tags: Record<string, unknown>;
  source_kinds: string[];
  created_by: string;
  created_at_ms: number;
  updated_at_ms: number;
  record_count: number;
  workflow_status: string;
}

interface LiveQualityRow {
  workflow: string;
  event_kind: string;
  window_hours: number;
  event_count: number;
  success_count: number;
  failure_count: number;
  traced_count: number;
  success_rate: number;
  failure_rate: number;
  trace_coverage: number;
  avg_latency_ms: number;
  latest_event_at_ms: number;
  dataset_source: string;
}

interface EvaluationRecordsPayload {
  status: string;
  dataset_id: string;
  dataset?: EvaluationDataset;
  records: EvaluationRecord[];
  limit?: number;
  message?: string;
}

interface EvaluationRecord {
  dataset_record_id: string;
  inputs: unknown;
  expectations: unknown;
  source: unknown;
  tags: unknown;
}

interface EvaluationEvent {
  event_id: string;
  event_kind: string;
  workflow: string;
  status: string;
  subject_id: string;
  user_id: string;
  mlflow_run_id: string;
  mlflow_trace_id: string;
  mlflow_dataset_name: string;
  metrics: Record<string, unknown>;
  reason_codes: string[];
  created_at_ms: number;
}

function EvaluationPage() {
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["evaluation-overview"],
    queryFn: () =>
      fetchJson<EvaluationOverview>("/api/knowledge/evaluation/overview"),
    staleTime: 30_000,
  });

  const selectedDataset = useMemo(
    () =>
      data?.datasets.find((dataset) => dataset.dataset_id === selectedDatasetId) ??
      data?.datasets[0] ??
      null,
    [data?.datasets, selectedDatasetId],
  );

  const recordsQuery = useQuery({
    queryKey: ["evaluation-records", selectedDataset?.dataset_id],
    queryFn: () =>
      fetchJson<EvaluationRecordsPayload>(
        `/api/knowledge/evaluation/datasets/${encodeURIComponent(
          selectedDataset?.dataset_id ?? "",
        )}/records`,
      ),
    enabled: Boolean(selectedDataset),
    staleTime: 30_000,
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
            <CardTitle>Evaluation</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-destructive">
              Could not load BrickVision evaluation data.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Evaluation</h1>
        <p className="text-sm text-muted-foreground">
          MLflow-backed quality gates for BrickVision workflows: graph snapshots,
          HippoRAG2 retrieval, workspace suggestions, usecases, tool proofs, and
          cost controls.
        </p>
      </header>

      <ExecutiveSummary data={data} />

      <EvaluationCategoriesTable categories={data.evaluation_categories} links={data.links} />

      <LiveQualityCard rows={data.live_quality} />

      <div className="grid gap-4 lg:grid-cols-2">
        <QualityGatesCard scorerRuns={data.latest_scorer_runs} links={data.links} />
        <RecentEventsCard events={data.recent_events} links={data.links} />
      </div>

      {data.registry.status !== "ready" ? (
        <Card className="border-amber-500/40">
          <CardHeader>
            <CardTitle>Evaluation Registry Needs Setup</CardTitle>
            <CardDescription>{data.registry.message}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              Run the MLflow dataset sync after configuring Databricks credentials
              and a serverless SQL warehouse.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <WorkflowCoverageCard workflows={data.workflows} />
        <ContractCard data={data} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <DatasetsCard
          datasets={data.datasets}
          selectedDatasetId={selectedDataset?.dataset_id ?? null}
          onSelect={setSelectedDatasetId}
        />
        <RecordsCard
          dataset={selectedDataset}
          payload={recordsQuery.data}
          isLoading={recordsQuery.isLoading}
          error={recordsQuery.error}
        />
      </div>
    </div>
  );
}

function ExecutiveSummary({ data }: { data: EvaluationOverview }) {
  const liveTraceTotal = data.live_quality.reduce(
    (sum, row) => sum + row.traced_count,
    0,
  );
  const liveEventTotal = data.live_quality.reduce(
    (sum, row) => sum + row.event_count,
    0,
  );
  const blockingReasons = data.evaluation_categories
    .filter((category) => category.status === "failed" || category.status === "warning")
    .slice(0, 3);

  return (
    <Card
      className={cn(
        "border-l-4",
        statusBorderClass(data.summary.overall_status),
      )}
    >
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              Evaluation Health
              <StatusPill status={data.summary.overall_status} />
            </CardTitle>
            <CardDescription>
              Promotion readiness is derived from curated gates, live quality,
              MLflow trace coverage, and category-level blockers.
            </CardDescription>
          </div>
          <MlflowLinks runId="" traceId="" links={data.links} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-4">
          <MetricCard
            title="Categories"
            value={`${data.summary.category_pass_count}/${data.evaluation_categories.length}`}
            detail={`${data.summary.category_warning_count} warning · ${data.summary.category_fail_count} fail · ${data.summary.category_not_scored_count} not scored`}
            ok={data.summary.category_fail_count === 0}
            Icon={CheckCircle2}
          />
          <MetricCard
            title="Curated Gates"
            value={`${data.summary.latest_scorer_pass_count}/${data.summary.latest_scorer_run_count}`}
            detail="Latest scorer runs passing"
            ok={
              data.summary.latest_scorer_run_count > 0 &&
              data.summary.latest_scorer_fail_count === 0
            }
            Icon={ClipboardCheck}
          />
          <MetricCard
            title="Live Trace Coverage"
            value={formatRatio(liveTraceTotal, liveEventTotal)}
            detail={`${liveTraceTotal}/${liveEventTotal} live events traced`}
            ok={liveEventTotal > 0 && liveTraceTotal / liveEventTotal >= 0.8}
            Icon={Activity}
          />
          <MetricCard
            title="Eval Sets"
            value={`${data.summary.dataset_count}`}
            detail={`${data.summary.record_count} records · ${data.summary.workflow_count} workflows`}
            ok={data.summary.dataset_count > 0}
            Icon={Database}
          />
        </div>
        {blockingReasons.length ? (
          <div className="rounded-md bg-muted p-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Top Reasons
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              {blockingReasons.map((category) => (
                <div key={category.id} className="text-sm">
                  <div className="font-medium">{category.title}</div>
                  <p className="text-xs text-muted-foreground">
                    {category.reason_summary}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EvaluationCategoriesTable({
  categories,
  links,
}: {
  categories: EvaluationCategory[];
  links: EvaluationOverview["links"];
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b bg-muted/30">
        <CardTitle className="flex items-center gap-2">
          Evaluation Results
          <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {categories.length} categories
          </span>
        </CardTitle>
        <CardDescription>
          Category-level pass/fail decisions with evidence denominators and
          explainable reasons.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 p-4">
        <div className="hidden grid-cols-[minmax(220px,1.35fr)_120px_130px_minmax(170px,0.9fr)_minmax(260px,1.4fr)_90px] gap-4 px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground lg:grid">
          <div>Category</div>
          <div>Scope</div>
          <div>Status</div>
          <div>Evidence</div>
          <div>Reason</div>
          <div className="text-right">Links</div>
        </div>
        <div className="space-y-3">
          {categories.map((category) => (
            <EvaluationCategoryRow
              key={category.id}
              category={category}
              links={links}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function EvaluationCategoryRow({
  category,
  links,
}: {
  category: EvaluationCategory;
  links: EvaluationOverview["links"];
}) {
  return (
    <div
      className={cn(
        "group relative overflow-visible rounded-xl border bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md",
        statusRingClass(category.status),
      )}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(220px,1.35fr)_120px_130px_minmax(170px,0.9fr)_minmax(260px,1.4fr)_90px] lg:items-center">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <StatusDot status={category.status} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{category.title}</div>
              <div className="truncate text-xs text-muted-foreground">
                {category.workflow}
              </div>
            </div>
          </div>
        </div>
        <div>
          <span className="inline-flex rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
            {category.scope}
          </span>
        </div>
        <div>
          <StatusPill status={category.status} />
        </div>
        <EvidenceMeter category={category} />
        <ReasonTooltip category={category} />
        <div className="flex justify-start lg:justify-end">
          <MlflowLinks
            runId={category.mlflow_run_id}
            traceId={category.mlflow_trace_id}
            links={links}
            compact
          />
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-xs text-muted-foreground lg:hidden">
        <span>Last run {formatDate(category.created_at_ms)}</span>
        <span>{category.evidence}</span>
      </div>
      <div className="mt-3 hidden items-center justify-between border-t pt-3 text-xs text-muted-foreground lg:flex">
        <span>Last run {formatDate(category.created_at_ms)}</span>
        <span>{category.next_action}</span>
      </div>
    </div>
  );
}

function EvidenceMeter({ category }: { category: EvaluationCategory }) {
  const ratio = category.denominator
    ? Math.max(0, Math.min(1, category.numerator / category.denominator))
    : 0;

  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="truncate text-sm font-medium">{category.evidence}</span>
        <span className="text-xs text-muted-foreground">
          {category.denominator ? `${category.numerator}/${category.denominator}` : "n/a"}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full", statusBarClass(category.status))}
          style={{ width: `${Math.round(ratio * 100)}%` }}
        />
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: EvaluationCategoryStatus }) {
  return (
    <span
      className={cn(
        "grid h-9 w-9 shrink-0 place-items-center rounded-full",
        statusIconClass(status),
      )}
      aria-hidden="true"
    >
      {status === "passed" ? (
        <CheckCircle2 className="h-4 w-4" />
      ) : status === "failed" ? (
        <XCircle className="h-4 w-4" />
      ) : (
        <AlertTriangle className="h-4 w-4" />
      )}
    </span>
  );
}

function ReasonTooltip({ category }: { category: EvaluationCategory }) {
  return (
    <div className="group/reason relative inline-flex max-w-full items-center gap-2">
      <span className="text-sm text-muted-foreground">
        {category.reason_summary}
      </span>
      <button
        type="button"
        className="shrink-0 rounded-full border border-border bg-background p-1 text-muted-foreground shadow-sm hover:text-foreground"
        aria-label={`Why ${category.title} is ${category.status}`}
      >
        <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      <div className="pointer-events-none absolute right-0 top-8 z-20 hidden w-96 rounded-xl border border-border bg-background p-4 text-xs text-foreground shadow-xl group-hover/reason:block">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="font-semibold">{category.title}</span>
          <StatusPill status={category.status} compact />
        </div>
        <div className="space-y-2">
          {category.reason_details.map((detail, index) => (
            <p key={`${category.id}:${index}`}>{detail}</p>
          ))}
          <p className="border-t pt-2 font-medium">Next: {category.next_action}</p>
        </div>
      </div>
    </div>
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

function WorkflowCoverageCard({ workflows }: { workflows: EvaluationWorkflow[] }) {
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Workflow Coverage</CardTitle>
        <CardDescription>
          Every BrickVision workflow should have curated MLflow evaluation
          records before it is treated as production-ready.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {workflows.map((workflow) => (
          <div
            key={workflow.workflow}
            className="grid gap-2 rounded-md border border-border p-3 md:grid-cols-[1fr_auto_auto]"
          >
            <div>
              <div className="font-medium">{workflow.title}</div>
              <div className="text-xs text-muted-foreground">
                {workflow.workflow}
              </div>
            </div>
            <div className="text-sm text-muted-foreground">
              {workflow.dataset_count} datasets
            </div>
            <div
              className={cn(
                "text-sm font-medium",
                workflow.status === "ready" || workflow.status === "passed"
                  ? "text-emerald-600"
                  : "text-amber-600",
              )}
            >
              {workflow.latest_scorer_run
                ? workflow.latest_scorer_run.status
                : `${workflow.record_count} records`}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function LiveQualityCard({ rows }: { rows: LiveQualityRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Live Quality</CardTitle>
        <CardDescription>
          Real runtime events from the last 24 hours. These are population
          denominators, not curated golden-set scores.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No live evaluation events in the current window. Search, Ask, and
            workflow operations will populate this section.
          </p>
        ) : (
          rows.map((row) => (
            <div
              key={`${row.workflow}:${row.event_kind}`}
              className="grid gap-3 rounded-md border border-border p-3 md:grid-cols-[1fr_auto_auto_auto]"
            >
              <div>
                <div className="font-medium">{row.workflow}</div>
                <div className="text-xs text-muted-foreground">
                  {row.event_kind} · last {row.window_hours}h · live events
                </div>
              </div>
              <LiveMetric label="n" value={String(row.event_count)} />
              <LiveMetric label="success" value={formatPercent(row.success_rate)} />
              <LiveMetric label="traced" value={formatPercent(row.trace_coverage)} />
              <div className="text-xs text-muted-foreground md:col-span-4">
                {row.failure_count} failed/blocked · avg latency{" "}
                {formatDuration(row.avg_latency_ms)} · latest{" "}
                {formatDate(row.latest_event_at_ms)}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function LiveMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}

function QualityGatesCard({
  scorerRuns,
  links,
}: {
  scorerRuns: EvaluationScorerRun[];
  links: EvaluationOverview["links"];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest Quality Gate Results</CardTitle>
        <CardDescription>
          Workflow-specific scorers persisted by the scheduled MLflow evaluation
          runner.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {scorerRuns.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No scorer runs have been recorded yet. Trigger `bv_evaluation_scorers`
            after syncing MLflow evaluation datasets.
          </p>
        ) : (
          scorerRuns.map((run) => (
            <div key={run.workflow} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{run.workflow}</div>
                  <div className="text-xs text-muted-foreground">
                    {run.mlflow_dataset_name || run.subject_id} ·{" "}
                    {formatDate(run.created_at_ms)}
                  </div>
                </div>
                <StatusPill status={run.status} />
              </div>
              <MlflowLinks
                runId={run.mlflow_run_id}
                traceId={run.mlflow_trace_id}
                links={links}
              />
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {run.scorer_results.map((result) => (
                  <div key={result.name} className="rounded-md bg-muted p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-medium">{result.label}</div>
                      <StatusPill status={result.status} compact />
                    </div>
                    <div className="mt-1 text-xl font-semibold">
                      {formatPercent(result.value)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      Gate: {formatPercent(result.threshold)}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {result.business_label}
                    </p>
                  </div>
                ))}
              </div>
              {run.reason_codes.length ? (
                <div className="mt-3 text-xs text-amber-600">
                  {run.reason_codes.join(", ")}
                </div>
              ) : null}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function ContractCard({ data }: { data: EvaluationOverview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>MLflow Dataset Contract</CardTitle>
        <CardDescription>{data.contract.storage}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <KeyValue label="Required" value={data.contract.required_fields.join(", ")} />
        <KeyValue label="Optional" value={data.contract.optional_fields.join(", ")} />
        <KeyValue
          label="Reserved expectations"
          value={data.contract.expectation_reserved_keys.join(", ")}
        />
        <KeyValue
          label="Sources"
          value={data.contract.supported_sources.join(", ")}
        />
        <KeyValue
          label="Max records"
          value={String(data.contract.max_records_per_dataset)}
        />
        <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
          {data.contract.next_action}
        </p>
      </CardContent>
    </Card>
  );
}

function RecentEventsCard({
  events,
  links,
}: {
  events: EvaluationEvent[];
  links: EvaluationOverview["links"];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Evaluation Events</CardTitle>
        <CardDescription>
          Automatically emitted from BrickVision UI, API, and workflow operations.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No evaluation events have been recorded yet. Search and Ask operations
            will start populating this stream when the API can write to UC.
          </p>
        ) : (
          events.map((event) => (
            <div
              key={event.event_id}
              className="grid gap-2 rounded-md border border-border p-3 md:grid-cols-[1fr_auto]"
            >
              <div className="min-w-0">
                <div className="font-medium">
                  {event.event_kind} · {event.workflow}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {event.subject_id} · {formatDate(event.created_at_ms)}
                </div>
                <div className="mt-2 grid gap-1 text-xs text-muted-foreground md:grid-cols-2">
                  {event.mlflow_dataset_name ? (
                    <span className="truncate">Dataset: {event.mlflow_dataset_name}</span>
                  ) : null}
                  {event.reason_codes.length ? (
                    <span className="truncate">
                      Reasons: {event.reason_codes.join(", ")}
                    </span>
                  ) : null}
                </div>
                <MlflowLinks
                  runId={event.mlflow_run_id}
                  traceId={event.mlflow_trace_id}
                  links={links}
                />
                {Object.keys(event.metrics).length ? (
                  <div className="mt-2">
                    <JsonBlock label="Metrics" value={event.metrics} />
                  </div>
                ) : null}
              </div>
              <div
                className={cn(
                  "text-sm font-medium",
                  event.status === "observed" || event.status === "passed"
                    ? "text-emerald-600"
                    : "text-amber-600",
                )}
              >
                {event.status}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function DatasetsCard({
  datasets,
  selectedDatasetId,
  onSelect,
}: {
  datasets: EvaluationDataset[];
  selectedDatasetId: string | null;
  onSelect: (datasetId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Registered Eval Sets</CardTitle>
        <CardDescription>
          These are MLflow evaluation datasets registered for BrickVision.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {datasets.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No evaluation datasets are registered yet. Curate JSONL records and
            run the sync script to create MLflow datasets.
          </p>
        ) : (
          datasets.map((dataset) => (
            <button
              key={dataset.dataset_id}
              type="button"
              onClick={() => onSelect(dataset.dataset_id)}
              className={cn(
                "w-full rounded-md border border-border p-3 text-left transition-colors",
                selectedDatasetId === dataset.dataset_id
                  ? "bg-primary/10 text-primary"
                  : "hover:bg-accent",
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-medium">{dataset.name}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {dataset.workflow} · {dataset.uc_table_name}
                  </div>
                </div>
                <span className="shrink-0 text-sm font-medium">
                  {dataset.record_count}
                </span>
              </div>
            </button>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function RecordsCard({
  dataset,
  payload,
  isLoading,
  error,
}: {
  dataset: EvaluationDataset | null;
  payload: EvaluationRecordsPayload | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (!dataset) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Dataset Records</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Select a registered evaluation dataset to preview records.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Dataset Records</CardTitle>
          <CardDescription>
            {dataset.workflow} · {dataset.uc_table_name}
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" disabled>
          MLflow owns edits
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 rounded-md bg-muted p-3 text-xs md:grid-cols-2">
          <KeyValue label="Experiment" value={dataset.mlflow_experiment_id || "not set"} />
          <KeyValue label="Status" value={dataset.workflow_status} />
          <KeyValue label="Sources" value={dataset.source_kinds.join(", ") || "none"} />
          <KeyValue label="Updated" value={formatDate(dataset.updated_at_ms)} />
        </div>
        {isLoading ? (
          <PanelSkeleton />
        ) : error ? (
          <p className="text-sm text-destructive">
            Could not load dataset records. Check the registered UC table shape and
            SQL warehouse access.
          </p>
        ) : payload?.message ? (
          <p className="text-sm text-muted-foreground">{payload.message}</p>
        ) : payload?.records.length ? (
          payload.records.map((record) => (
            <div
              key={record.dataset_record_id}
              className="rounded-md border border-border p-3"
            >
              <div className="mb-2 text-xs font-medium text-muted-foreground">
                {record.dataset_record_id}
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <JsonBlock label="Inputs" value={record.inputs} />
                <JsonBlock label="Expectations" value={record.expectations} />
                <JsonBlock label="Source" value={record.source} />
                <JsonBlock label="Tags" value={record.tags} />
              </div>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            This dataset is registered but has no readable records yet.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div>{value}</div>
    </div>
  );
}

function MlflowLinks({
  runId,
  traceId,
  links,
  compact = false,
}: {
  runId: string;
  traceId: string;
  links: EvaluationOverview["links"];
  compact?: boolean;
}) {
  const runUrl = mlflowRunUrl(runId, links);
  const traceUrl = mlflowTraceUrl(traceId, links);

  if (!runId && !traceId) {
    return null;
  }

  return (
    <div className={cn("flex flex-wrap gap-2 text-xs", compact ? "" : "mt-2")}>
      {runId ? (
        runUrl ? (
          <a
            className={cn(
              "font-medium text-primary hover:underline",
              compact && "rounded-full bg-primary/10 px-2 py-1",
            )}
            href={runUrl}
            target="_blank"
            rel="noreferrer"
          >
            MLflow run
          </a>
        ) : (
          <span className="truncate text-muted-foreground">MLflow run: {runId}</span>
        )
      ) : null}
      {traceId ? (
        traceUrl ? (
          <a
            className={cn(
              "font-medium text-primary hover:underline",
              compact && "rounded-full bg-primary/10 px-2 py-1",
            )}
            href={traceUrl}
            target="_blank"
            rel="noreferrer"
          >
            Trace
          </a>
        ) : (
          <span className="truncate text-muted-foreground">Trace: {traceId}</span>
        )
      ) : null}
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-muted-foreground">{label}</div>
      <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">
        {formatJson(value)}
      </pre>
    </div>
  );
}

function mlflowRunUrl(runId: string, links: EvaluationOverview["links"]): string {
  if (!runId || !links.databricks_host || !links.mlflow_experiment_id) {
    return "";
  }
  return `${links.databricks_host}/ml/experiments/${encodeURIComponent(
    links.mlflow_experiment_id,
  )}/evaluation-runs?selectedRunUuid=${encodeURIComponent(runId)}`;
}

function mlflowTraceUrl(traceId: string, links: EvaluationOverview["links"]): string {
  if (!traceId || !links.databricks_host || !links.mlflow_experiment_id) {
    return "";
  }
  return `${links.databricks_host}/ml/experiments/${encodeURIComponent(
    links.mlflow_experiment_id,
  )}/traces?selectedTraceId=${encodeURIComponent(traceId)}`;
}

function StatusPill({
  status,
  compact = false,
}: {
  status: string;
  compact?: boolean;
}) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 font-medium",
        compact ? "text-[10px]" : "text-xs",
        status === "passed" || status === "observed"
          ? "bg-emerald-500/10 text-emerald-600"
          : status === "failed"
            ? "bg-destructive/10 text-destructive"
          : status === "not_scored"
            ? "bg-muted text-muted-foreground"
            : "bg-amber-500/10 text-amber-600",
      )}
    >
      {status.replace("_", " ")}
    </span>
  );
}

function statusBorderClass(status: EvaluationCategoryStatus): string {
  if (status === "passed") {
    return "border-l-emerald-500";
  }
  if (status === "failed") {
    return "border-l-destructive";
  }
  if (status === "not_scored") {
    return "border-l-muted-foreground";
  }
  return "border-l-amber-500";
}

function statusRingClass(status: EvaluationCategoryStatus): string {
  if (status === "passed") {
    return "border-emerald-500/20 hover:border-emerald-500/50";
  }
  if (status === "failed") {
    return "border-destructive/30 hover:border-destructive/60";
  }
  if (status === "not_scored") {
    return "border-muted-foreground/20 hover:border-muted-foreground/40";
  }
  return "border-amber-500/30 hover:border-amber-500/60";
}

function statusIconClass(status: EvaluationCategoryStatus): string {
  if (status === "passed") {
    return "bg-emerald-500/10 text-emerald-600";
  }
  if (status === "failed") {
    return "bg-destructive/10 text-destructive";
  }
  if (status === "not_scored") {
    return "bg-muted text-muted-foreground";
  }
  return "bg-amber-500/10 text-amber-600";
}

function statusBarClass(status: EvaluationCategoryStatus): string {
  if (status === "passed") {
    return "bg-emerald-500";
  }
  if (status === "failed") {
    return "bg-destructive";
  }
  if (status === "not_scored") {
    return "bg-muted-foreground";
  }
  return "bg-amber-500";
}

function formatJson(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "{}";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function formatDate(value: number): string {
  if (!value) {
    return "unknown time";
  }
  return new Date(value).toLocaleString();
}

function formatPercent(value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "not run";
  }
  return `${Math.round(value * 100)}%`;
}

function formatRatio(numerator: number, denominator: number): string {
  if (!denominator) {
    return "0%";
  }
  return formatPercent(numerator / denominator);
}

function formatDuration(value: number): string {
  if (!value || Number.isNaN(value)) {
    return "n/a";
  }
  if (value < 1000) {
    return `${Math.round(value)}ms`;
  }
  return `${(value / 1000).toFixed(1)}s`;
}
