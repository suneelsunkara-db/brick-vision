import { Link, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Hammer, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";

export const Route = createFileRoute("/migrations")({
  component: MigrationsPage,
});

const TASKS = [
  {
    id: "sql_transpile",
    title: "Transpile Legacy SQL",
    icon: Hammer,
    body: "Convert Teradata-style SQL into Databricks notebook output with Lakebridge Switch.",
    state: "Live",
  },
  {
    id: "code_convert",
    title: "Convert PySpark/Python",
    icon: GitBranch,
    body: "Convert legacy PySpark/Python scripts into Databricks notebook code.",
    state: "Live",
  },
  {
    id: "assessment",
    title: "Run Assessment",
    icon: Search,
    body: "Check Lakebridge Analyzer readiness and source-system support before conversion.",
    state: "Live",
  },
] as const;

interface MigrationStep {
  step_id: string;
  status: string;
  label: string;
  updated_at_ms?: number;
}

interface MigrationRun {
  migration_run_id: string;
  usecase_id?: string;
  workflow_type: string;
  status: string;
  phase?: string;
  title?: string;
  durable?: boolean;
  created_at_ms: number;
  updated_at_ms: number;
  steps?: MigrationStep[];
  inputs?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: { error_kind?: string; message?: string } | null;
  next_action?: string;
}

interface MigrationRunsPayload {
  status: string;
  migration_runs: MigrationRun[];
  next_action?: string;
}

function MigrationsPage() {
  const queryClient = useQueryClient();
  const [workflowType, setWorkflowType] = useState("code_convert");
  const [usecaseId, setUsecaseId] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["migration-runs"],
    queryFn: () => fetchJson<MigrationRunsPayload>("/api/knowledge/migration-runs"),
    refetchInterval: 5_000,
  });
  const startMutation = useMutation({
    mutationFn: () =>
      fetchJson<MigrationRun>("/api/knowledge/migration-runs", {
        method: "POST",
        body: JSON.stringify({
          workflow_type: workflowType,
          usecase_id: usecaseId.trim() || undefined,
          inputs: {},
        }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["migration-runs"] });
    },
  });

  if (isLoading) return <PanelSkeleton />;
  const runs = data?.migration_runs ?? [];
  const activeRuns = runs.filter((run) => ["queued", "running"].includes(run.status));
  const successfulRuns = runs.filter((run) => run.status === "completed");
  const failedRuns = runs.filter((run) => ["blocked", "failed"].includes(run.status));
  const currentRun = activeRuns[0] ?? successfulRuns[0] ?? runs[0];
  const historyRuns = runs.filter((run) => run.migration_run_id !== currentRun?.migration_run_id);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" aria-hidden="true" />
          <h1 className="text-2xl font-semibold tracking-tight">Migration Workflows</h1>
        </div>
        <p className="max-w-4xl text-sm text-muted-foreground">
          Migration is a skill-powered workflow, not a usecase type. A KG
          opportunity may create or link to a migration run, while the workflow
          itself owns assessment, conversion, validation, and artifacts.
        </p>
      </header>

      <Card className="border-primary/30 bg-primary/5">
        <CardHeader>
          <CardTitle className="text-base">Choose a Migration Task</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <p>Pick the asset type you want to migrate. BrickVision will use the configured UC Volume source and output paths unless you override them later.</p>
          <div className="grid gap-3 md:grid-cols-3">
            {TASKS.map((task) => {
              const Icon = task.icon;
              const selected = workflowType === task.id;
              const disabled = false;
              return (
                <button
                  key={task.id}
                  type="button"
                  disabled={disabled}
                  onClick={() => !disabled && setWorkflowType(task.id)}
                  className={[
                    "rounded-lg border p-4 text-left transition-colors",
                    selected
                      ? "border-primary/60 bg-primary/10"
                      : "border-border bg-background/70 hover:bg-muted/20",
                    disabled ? "cursor-not-allowed opacity-60" : "",
                  ].join(" ")}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2 font-medium text-foreground">
                      <Icon className="h-4 w-4 text-primary" aria-hidden="true" />
                      {task.title}
                    </div>
                    <StatusBadge status={task.state} compact />
                  </div>
                  <p className="mt-3 text-xs text-muted-foreground">{task.body}</p>
                </button>
              );
            })}
          </div>
          <details className="rounded border border-border bg-background/60 p-3">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
              Advanced: attach this run to a KG opportunity
            </summary>
            <label className="mt-3 block space-y-1">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Opportunity/usecase id
              </span>
              <input
                className="w-full rounded border border-border bg-background px-2 py-2 font-mono text-sm text-foreground"
                value={usecaseId}
                placeholder="optional"
                onChange={(event) => setUsecaseId(event.target.value)}
              />
            </label>
          </details>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={startMutation.isPending}
              onClick={() => startMutation.mutate()}
            >
              {startMutation.isPending ? "Starting..." : `Start ${workflowLabel(workflowType)}`}
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="/usecases">Attach from Opportunities</Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {activeRuns.length > 0 ? "Current Run" : "Latest Result"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {currentRun ? (
            <MigrationRunCard run={currentRun} prominent />
          ) : (
            <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              {data?.next_action ?? "No migration workflow runs have been started yet."}
            </div>
          )}
        </CardContent>
      </Card>

      {historyRuns.length > 0 && (
        <details className="rounded-xl border border-border bg-background/70 p-4">
          <summary className="cursor-pointer text-sm font-medium">
            Run history ({historyRuns.length})
            {failedRuns.length > 0 ? `, ${failedRuns.length} need attention` : ""}
          </summary>
          <div className="mt-4 space-y-3">
            {historyRuns.map((run) => (
              <MigrationRunCard key={run.migration_run_id} run={run} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function MigrationRunCard({ run, prominent = false }: { run: MigrationRun; prominent?: boolean }) {
  const resultStatus = textValue(run.result, "status");
  const switchRun = asRecord(run.result?.switch_run);
  const sqlRun = asRecord(run.result?.lakebridge_sql_run);
  const liveRun = switchRun ?? sqlRun;
  const migrationArtifact = asRecord(run.result?.migration_artifact);
  const assessmentArtifact = asRecord(run.result?.assessment_artifact);
  const readinessChecks = Array.isArray(run.result?.readiness_checks)
    ? run.result.readiness_checks
    : [];
  const preview =
    textValue(switchRun, "converted_artifact_preview") ??
    textValue(migrationArtifact, "raw_databricks_sql") ??
    textValue(migrationArtifact, "remediated_databricks_sql");
  const previewName =
    textValue(switchRun, "converted_artifact_name") ??
    textValue(sqlRun, "generated_file") ??
    textValue(migrationArtifact, "raw_output_path");
  const stages = Array.isArray(liveRun?.stages) ? liveRun.stages : [];
  const runPageUrl = stages
    .map((item) => asRecord(item))
    .map((item) => textValue(item, "run_page_url"))
    .find(Boolean);
  return (
    <div className={`rounded-lg border border-border bg-background/70 ${prominent ? "p-4" : "p-3"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-medium">{run.title || workflowLabel(run.workflow_type)}</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {run.migration_run_id}
          </div>
          {run.usecase_id && (
            <div className="mt-1 font-mono text-[10px] text-muted-foreground">
              linked opportunity: {run.usecase_id}
            </div>
          )}
        </div>
        <StatusBadge status={run.status} />
      </div>
      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
        <Detail label="Workflow" value={workflowLabel(run.workflow_type)} />
        <Detail label="Phase" value={run.phase || "unknown"} />
        <Detail label="Durable" value={run.durable ? "yes" : "sidecar only"} />
      </div>
      {run.steps && run.steps.length > 0 && (
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          {run.steps.map((step) => (
            <div key={step.step_id} className="rounded border border-border bg-muted/20 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-foreground/90">{step.step_id}</span>
                <StatusBadge status={step.status} compact />
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground">{step.label}</p>
            </div>
          ))}
        </div>
      )}
      {(run.next_action || resultStatus || run.error?.message) && (
        <div className="mt-3 rounded bg-muted/30 p-2 text-xs text-muted-foreground">
          {run.error?.message ?? resultStatus ?? run.next_action}
        </div>
      )}
      {preview && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Generated artifact{previewName ? `: ${previewName}` : ""}
          </div>
          <pre className="max-h-56 overflow-auto rounded border border-border bg-background/70 p-2 text-[10px] leading-relaxed text-foreground/90">
            {preview}
          </pre>
        </div>
      )}
      {assessmentArtifact && (
        <details className="mt-3 rounded border border-border bg-muted/10 p-3" open={prominent}>
          <summary className="cursor-pointer text-xs font-semibold">Assessment readiness</summary>
          <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
            <Detail label="Source system" value={textValue(assessmentArtifact, "source_system") ?? "unknown"} />
            <Detail label="Support status" value={textValue(assessmentArtifact, "support_status") ?? "unknown"} />
            <Detail label="Inventory" value={textValue(assessmentArtifact, "inventory_status") ?? "unknown"} />
            <Detail label="Complexity" value={textValue(assessmentArtifact, "complexity_status") ?? "unknown"} />
          </div>
          {readinessChecks.length > 0 && (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {readinessChecks.map((item, index) => {
                const check = asRecord(item);
                if (!check) return null;
                return (
                  <div key={`${textValue(check, "name") ?? "check"}:${index}`} className="rounded border border-border bg-background/70 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-foreground/90">{textValue(check, "name") ?? "check"}</span>
                      <StatusBadge status={textValue(check, "status") ?? "unknown"} compact />
                    </div>
                    <p className="mt-1 text-[10px] text-muted-foreground">{textValue(check, "message")}</p>
                  </div>
                );
              })}
            </div>
          )}
        </details>
      )}
      {liveRun && (
        <details className="mt-3 rounded border border-border bg-muted/10 p-3">
          <summary className="cursor-pointer text-xs font-semibold">Technical Lakebridge details</summary>
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
            <Detail label="Return code" value={textValue(liveRun, "return_code") ?? "unknown"} />
            {textValue(liveRun, "output_volume_file") && (
              <Detail label="Output Volume file" value={textValue(liveRun, "output_volume_file") ?? ""} />
            )}
            {textValue(liveRun, "output_folder") && (
              <Detail label="Workspace output" value={textValue(liveRun, "output_folder") ?? ""} />
            )}
            {textValue(liveRun, "stdout") && (
              <Detail label="Output copy" value={textValue(liveRun, "stdout") ?? ""} />
            )}
          </div>
          {runPageUrl && (
            <a
              href={runPageUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex text-[10px] text-primary hover:underline"
            >
              Open Databricks run
            </a>
          )}
          {stages.length > 0 && (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {stages.map((item, index) => {
                const stage = asRecord(item);
                if (!stage) return null;
                return (
                  <div key={`${textValue(stage, "stage") ?? "stage"}:${index}`} className="rounded border border-border bg-background/70 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-foreground/90">{textValue(stage, "stage") ?? "stage"}</span>
                      <StatusBadge status={textValue(stage, "return_code") === "0" ? "completed" : "running"} compact />
                    </div>
                    {textValue(stage, "run_id") && (
                      <p className="mt-1 font-mono text-[10px] text-muted-foreground">run {textValue(stage, "run_id")}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </details>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-2">
      <div className="text-[10px] uppercase tracking-wide">{label}</div>
      <div className="mt-1 break-all font-mono text-[10px] text-foreground/90">{value}</div>
    </div>
  );
}

function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  return (
    <span
      className={[
        "shrink-0 rounded font-medium uppercase tracking-wide",
        compact ? "px-1.5 py-0.5 text-[9px]" : "px-2 py-1 text-[10px]",
        statusClass(status),
      ].join(" ")}
    >
      {status.replaceAll("_", " ")}
    </span>
  );
}

function statusClass(status: string) {
  if (["completed", "passed", "Live"].includes(status)) {
    return "bg-emerald-500/10 text-emerald-400";
  }
  if (["queued", "running", "pending"].includes(status)) {
    return "bg-sky-500/10 text-sky-300";
  }
  if (["failed", "blocked", "skipped", "interrupted"].includes(status)) {
    return "bg-destructive/10 text-destructive";
  }
  if (["not_available", "Coming next"].includes(status)) {
    return "bg-muted/50 text-muted-foreground";
  }
  return "bg-muted/50 text-muted-foreground";
}

function workflowLabel(value: string) {
  if (value === "assessment") return "Assessment";
  if (value === "sql_transpile") return "SQL Transpile";
  if (value === "code_convert") return "Code Convert";
  return value.replaceAll("_", " ");
}

function textValue(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  if (value === null || value === undefined || value === "") return undefined;
  return String(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
