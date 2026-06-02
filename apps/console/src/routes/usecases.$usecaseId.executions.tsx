import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Boxes,
  CheckCircle2,
  Clock3,
  Play,
  RefreshCw,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/usecases/$usecaseId/executions")({
  component: UsecaseExecutionMonitorRoutePage,
});

type Family = "SQL" | "PySpark" | "ML" | "Migration" | "Code Convert";
type StartExecutionRequest = {
  family: Family;
  inputs?: Record<string, string>;
};

interface UsecasePayload {
  status: string;
  title?: string;
  outcome?: string;
  artifact_plan?: {
    plan_id?: string;
    status?: string;
    steps?: Array<{
      step_id?: string;
      title?: string;
      tool_family?: string;
      expected_output?: string;
    }>;
  };
}

interface ToolProof {
  family: string;
  status: string;
  skill_id?: string;
  proof_id?: string;
  next_action?: string;
  created_at_ms?: number;
  result?: Record<string, unknown>;
}

interface ToolProofsPayload {
  status: string;
  proofs: ToolProof[];
}

interface ExecutionStep {
  step_id: string;
  status: string;
  label: string;
  updated_at_ms?: number;
}

interface ExecutionRun {
  execution_id: string;
  usecase_id: string;
  family: string;
  status: string;
  created_at_ms: number;
  updated_at_ms: number;
  steps: ExecutionStep[];
  result?: ToolProof | null;
  error?: { error_kind?: string; message?: string } | null;
  next_action?: string;
  durable?: boolean;
}

interface ExecutionsPayload {
  status: string;
  executions: ExecutionRun[];
  next_action?: string;
}

const FAMILIES: Family[] = ["SQL", "PySpark", "ML", "Migration", "Code Convert"];
const ARTIFACT_SKILL_FAMILIES: Family[] = ["SQL", "PySpark", "ML"];

function UsecaseExecutionMonitorRoutePage() {
  const { usecaseId } = Route.useParams();
  return <UsecaseExecutionMonitorPage usecaseId={usecaseId} />;
}

export function UsecaseExecutionMonitorPage({ usecaseId }: { usecaseId: string }) {
  const queryClient = useQueryClient();

  const usecaseQuery = useQuery({
    queryKey: ["usecases", usecaseId],
    queryFn: () =>
      fetchJson<UsecasePayload>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}`,
      ),
  });
  const proofsQuery = useQuery({
    queryKey: ["usecases", usecaseId, "tool-proofs"],
    queryFn: () =>
      fetchJson<ToolProofsPayload>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/tool-proofs`,
      ),
    refetchInterval: 5_000,
  });
  const executionsQuery = useQuery({
    queryKey: ["usecases", usecaseId, "executions"],
    queryFn: () =>
      fetchJson<ExecutionsPayload>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/executions`,
      ),
    refetchInterval: 2_000,
  });

  const startMutation = useMutation({
    mutationFn: ({ family, inputs }: StartExecutionRequest) => {
      const body =
        family === "Code Convert"
          ? { ...(inputs ?? {}), source_technology: "python" }
          : (inputs ?? {});
      return fetchJson<ExecutionRun>(
        `/api/knowledge/usecases/${encodeURIComponent(
          usecaseId,
        )}/executions/${encodeURIComponent(family)}`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["usecases", usecaseId, "executions"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["usecases", usecaseId, "tool-proofs"],
      });
    },
  });

  if (usecaseQuery.isLoading || proofsQuery.isLoading || executionsQuery.isLoading) {
    return <PanelSkeleton />;
  }

  const usecase = usecaseQuery.data;
  const proofs = proofsQuery.data?.proofs ?? [];
  const executions = executionsQuery.data?.executions ?? [];
  const latestByFamily = latestExecutionByFamily(executions);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button asChild variant="outline" size="sm">
          <Link to="/usecases/$usecaseId" params={{ usecaseId }}>
            Back to Opportunity
          </Link>
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void executionsQuery.refetch();
            void proofsQuery.refetch();
          }}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          Refresh
        </Button>
      </div>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            Linked Skill and Workflow Runs
          </h1>
          <StatusPill status={proofsQuery.data?.status ?? "unknown"} />
        </div>
        <p className="max-w-4xl text-sm text-muted-foreground">
          Live run view for{" "}
          <span className="font-medium text-foreground">{usecase?.title}</span>.
          The usecase stays the KG opportunity; these runs are attached evidence
          from reusable skills and migration workflows.
        </p>
      </header>

      <Card className="border-primary/30 bg-primary/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Workflow className="h-4 w-4 text-primary" aria-hidden="true" />
            Attached Run Canvas
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-[0.9fr_1.25fr_0.85fr]">
          <div className="space-y-3">
            <RunGroup
              title="Artifact Skill Runs"
              description="SQL, PySpark, and ML skills build or prove artifacts for the opportunity."
              families={ARTIFACT_SKILL_FAMILIES}
              proofs={proofs}
              latestByFamily={latestByFamily}
              isPending={startMutation.isPending}
              onStart={(family) =>
                startMutation.mutate({
                  family,
                  inputs: undefined,
                })
              }
            />
            <MigrationWorkflowPointer />
          </div>

          <div className="rounded-xl border border-border bg-background/70 p-4">
            <div className="mb-4 flex items-center gap-2 text-sm font-medium">
              <Activity className="h-4 w-4 text-primary" aria-hidden="true" />
              Live Runs
            </div>
            {executions.length > 0 ? (
              <div className="space-y-4">
                {executions.slice(0, 6).map((run) => (
                  <ExecutionTimeline key={run.execution_id} run={run} />
                ))}
              </div>
            ) : (
              <EmptyState
                title="No monitor run yet"
                message="Start an artifact skill run here, or open Migration Workflows for Lakebridge runs."
              />
            )}
          </div>

          <div className="space-y-3">
            <ArtifactPlanCard plan={usecase?.artifact_plan} />
            <ProofEvidenceCard proofs={proofs} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Boxes className="h-4 w-4 text-primary" aria-hidden="true" />
            Deep Review Notes
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            This page observes skill and workflow runs linked to the opportunity.
            It should not be read as the usecase model itself: the usecase is the
            KG-derived reason, while these runs are the execution evidence.
          </p>
          <p>
            Migration work belongs to the workflow model: Assessment, Converter
            using SQL Transpile or Code Convert, then Validate or Reconcile. The
            current live monitor is the shared observation layer for those runs.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function RunGroup({
  title,
  description,
  families,
  proofs,
  latestByFamily,
  isPending,
  onStart,
}: {
  title: string;
  description: string;
  families: Family[];
  proofs: ToolProof[];
  latestByFamily: Partial<Record<Family, ExecutionRun>>;
  isPending: boolean;
  onStart: (family: Family) => void;
}) {
  return (
    <div className="space-y-2 rounded-xl border border-border bg-background/50 p-3">
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      </div>
      {families.map((family) => {
        const proof = proofs.find((item) => item.family === family);
        const run = latestByFamily[family];
        const isRunning = run && ["queued", "running"].includes(run.status);
        return (
          <FamilyNode
            key={family}
            family={family}
            proof={proof}
            run={run}
            isPending={isPending}
            onStart={() => onStart(family)}
            disabled={Boolean(isRunning)}
          />
        );
      })}
    </div>
  );
}

function FamilyNode({
  family,
  proof,
  run,
  isPending,
  disabled,
  onStart,
}: {
  family: Family;
  proof?: ToolProof;
  run?: ExecutionRun;
  isPending: boolean;
  disabled: boolean;
  onStart: () => void;
}) {
  const runProof = run?.result;
  const status =
    run && ["queued", "running"].includes(run.status)
      ? run.status
      : runProof?.status ?? proof?.status ?? run?.status ?? "not_run";
  const skillId =
    textValue(runProof?.result, "skill_id") ??
    runProof?.skill_id ??
    proof?.skill_id ??
    familySkillLabel(family);
  return (
    <div className="rounded-xl border border-border bg-background/70 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">{familyDisplayLabel(family)}</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {skillId}
          </div>
        </div>
        <StatusPill status={status} />
      </div>
      <p className="mt-3 line-clamp-3 text-xs text-muted-foreground">
        {runProof?.next_action ??
          run?.next_action ??
          proof?.next_action ??
          `Run the ${familyDisplayLabel(family)} proof.`}
      </p>
      {run?.execution_id && (
        <div className="mt-3 rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
          {run.execution_id}
        </div>
      )}
      <Button
        className="mt-4 w-full"
        size="sm"
        variant="outline"
        disabled={isPending || disabled}
        onClick={onStart}
      >
        <Play className="h-3.5 w-3.5" aria-hidden="true" />
        {disabled ? "Running..." : `Start ${familyDisplayLabel(family)}`}
      </Button>
    </div>
  );
}

function MigrationWorkflowPointer() {
  return (
    <div className="rounded-xl border border-border bg-background/50 p-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Migration Workflow Runs
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Lakebridge SQL Transpile and Code Convert are now owned by Migration
        Workflows. Link their completed runs back to opportunities as evidence
        instead of launching them as usecase execution families.
      </p>
      <Button asChild className="mt-3 w-full" size="sm" variant="outline">
        <Link to="/migrations">Open Migration Workflows</Link>
      </Button>
    </div>
  );
}

function ExecutionTimeline({ run }: { run: ExecutionRun }) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <StatusIcon status={run.status} />
          <div>
            <div className="text-sm font-medium">{run.family}</div>
            <div className="font-mono text-[10px] text-muted-foreground">
              {run.execution_id}
            </div>
          </div>
        </div>
        <StatusPill status={run.status} />
      </div>
      <div className="mt-4 space-y-2">
        {run.steps.map((step) => (
          <div key={step.step_id} className="flex items-start gap-3">
            <div
              className={cn(
                "mt-0.5 h-2.5 w-2.5 rounded-full",
                dotClass(step.status),
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium">{step.label}</span>
                <StatusPill status={step.status} compact />
              </div>
              <div className="mt-0.5 text-[10px] text-muted-foreground">
                {formatTime(step.updated_at_ms)}
              </div>
            </div>
          </div>
        ))}
      </div>
      {run.error?.message && (
        <div className="mt-3 rounded border border-destructive/30 bg-destructive/10 p-2 text-xs text-muted-foreground">
          {run.error.error_kind}: {run.error.message}
        </div>
      )}
      <ExecutionDetails run={run} />
    </div>
  );
}

function ExecutionDetails({ run }: { run: ExecutionRun }) {
  const proof = run.result ?? null;
  const execution = asRecord(proof?.result);
  if (!proof && !execution) return null;

  const dataPipelineRun = asRecord(execution?.data_pipeline_run);
  const metadata = asRecord(dataPipelineRun?.metadata);
  const sqlText = textValue(execution, "sql_text");
  const transformCode = textValue(execution, "transform_code");
  const generatedCode = sqlText || transformCode;
  const outputUri =
    textValue(execution, "output_uri") ??
    textValue(metadata, "output_uri") ??
    textValue(dataPipelineRun, "output_uri");
  const runId =
    textValue(execution, "run_id") ??
    textValue(dataPipelineRun, "run_id") ??
    textValue(metadata, "run_id");
  const rowCount =
    textValue(execution, "row_count") ??
    textValue(dataPipelineRun, "row_count") ??
    textValue(metadata, "row_count");
  const observedSchema = asRecord(dataPipelineRun?.observed_schema);
  const migrationArtifact = asRecord(execution?.migration_artifact);
  const migrationLivePreflight = asRecord(execution?.migration_live_preflight);
  const lakebridgeSqlRun = asRecord(execution?.lakebridge_sql_run);
  const codeConvertPreflight = asRecord(execution?.code_convert_preflight);
  const switchRun = asRecord(execution?.switch_run);
  const switchCommand = Array.isArray(execution?.switch_command)
    ? execution.switch_command.map((item) => String(item)).filter(Boolean)
    : [];
  const proofMode = textValue(execution, "proof_mode");
  const questions = Array.isArray(execution?.questions) ? execution.questions : [];
  const planKeys = [
    metadata?.statement_execution_plan ? "Statement Execution API plan" : null,
    metadata?.pyspark_task_plan ? "PySpark task plan" : null,
    metadata?.jobs_submit_plan ? "Jobs Runs Submit plan" : null,
  ].filter(Boolean);

  return (
    <div className="mt-4 rounded-lg border border-border bg-background/60 p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold">What executed</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {proof?.skill_id ?? textValue(execution, "skill_id") ?? familySkillLabel(run.family as Family)}
          </div>
        </div>
        <StatusPill status={proof?.status ?? textValue(execution, "status") ?? run.status} compact />
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <DetailItem label="Execution proof" value={proof?.proof_id ?? run.execution_id} />
        <DetailItem
          label="Executed"
          value={booleanValue(execution, "executed") === false ? "No" : "Yes"}
        />
        {outputUri && <DetailItem label="Output object" value={outputUri} />}
        {runId && <DetailItem label="Databricks run id" value={runId} />}
        {rowCount && <DetailItem label="Rows observed" value={rowCount} />}
        {proofMode && <DetailItem label="Proof mode" value={proofMode} />}
        {planKeys.length > 0 && (
          <DetailItem label="Databricks plans" value={planKeys.join(", ")} />
        )}
      </div>

      {(proof?.next_action || textValue(execution, "message")) && (
        <div className="mt-3 rounded bg-muted/30 p-2 text-xs text-muted-foreground">
          {textValue(execution, "message") ?? proof?.next_action}
        </div>
      )}

      {migrationArtifact && <MigrationArtifactDetails artifact={migrationArtifact} />}
      {lakebridgeSqlRun && <LakebridgeSqlRunDetails run={lakebridgeSqlRun} />}
      {migrationLivePreflight && (
        <MigrationLivePreflightDetails preflight={migrationLivePreflight} />
      )}
      {codeConvertPreflight && (
        <CodeConvertPreflightDetails
          preflight={codeConvertPreflight}
          switchCommand={switchCommand}
          switchRun={switchRun}
        />
      )}

      {generatedCode && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {sqlText ? "SQL submitted" : "PySpark code submitted"}
          </div>
          <pre className="max-h-44 overflow-auto rounded border border-border bg-muted/30 p-2 text-[10px] leading-relaxed text-foreground/90">
            {generatedCode}
          </pre>
        </div>
      )}

      {observedSchema && Object.keys(observedSchema).length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Observed output schema
          </div>
          <div className="grid gap-1 sm:grid-cols-2">
            {Object.entries(observedSchema).slice(0, 8).map(([name, type]) => (
              <div
                key={name}
                className="rounded bg-muted/30 px-2 py-1 font-mono text-[10px] text-muted-foreground"
              >
                {name}: {String(type)}
              </div>
            ))}
          </div>
        </div>
      )}

      {questions.length > 0 && (
        <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-muted-foreground">
          The skill returned {questions.length} follow-up question
          {questions.length === 1 ? "" : "s"} before it can complete execution.
        </div>
      )}
    </div>
  );
}

function CodeConvertPreflightDetails({
  preflight,
  switchCommand,
  switchRun,
}: {
  preflight: Record<string, unknown>;
  switchCommand: string[];
  switchRun: Record<string, unknown> | null;
}) {
  const checks = Array.isArray(preflight.checks) ? preflight.checks : [];
  const missing = Array.isArray(preflight.missing)
    ? preflight.missing.map((item) => String(item)).filter(Boolean)
    : [];
  const executedCommand = Array.isArray(switchRun?.command)
    ? switchRun.command.map((item) => String(item)).filter(Boolean)
    : [];
  const command = executedCommand.length > 0 ? executedCommand : switchCommand;
  const stages = Array.isArray(switchRun?.stages) ? switchRun.stages : [];
  const convertedArtifactPreview = textValue(switchRun, "converted_artifact_preview");
  const convertedArtifactName = textValue(switchRun, "converted_artifact_name");
  const generatedFiles = Array.isArray(switchRun?.generated_files)
    ? switchRun.generated_files.map((item) => String(item)).filter(Boolean)
    : [];

  return (
    <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/10 p-3">
      <div className="mb-2 text-xs font-semibold">PySpark Code Convert Preflight</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <DetailItem label="PySpark source Volume path" value={textValue(preflight, "source_path") ?? "not bound"} />
        <DetailItem label="Converted output Volume path" value={textValue(preflight, "output_path") ?? "not bound"} />
        <DetailItem
          label="Source technology"
          value={textValue(preflight, "source_technology") ?? "pyspark"}
        />
        <DetailItem
          label="Target technology"
          value={textValue(preflight, "target_technology") ?? "databricks"}
        />
        {textValue(preflight, "switch_config_path") && (
          <DetailItem label="Switch config path" value={textValue(preflight, "switch_config_path") ?? ""} />
        )}
      </div>

      <div className="mt-3 grid gap-1">
        {checks.map((item, index) => {
          const check = asRecord(item);
          if (!check) return null;
          return (
            <div
              key={textValue(check, "check_id") ?? String(index)}
              className="rounded border border-border bg-background/70 p-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-foreground/90">
                  {textValue(check, "check_id") ?? "preflight_check"}
                </span>
                <StatusPill status={textValue(check, "status") ?? "unknown"} compact />
              </div>
              <div className="mt-1 text-[10px] text-muted-foreground">
                {textValue(check, "message") ?? "No check message returned."}
              </div>
            </div>
          );
        })}
      </div>

      {missing.length > 0 && (
        <div className="mt-3 rounded border border-amber-500/30 bg-background/70 p-2 text-xs text-muted-foreground">
          Missing before real Switch PySpark conversion: {missing.join(", ")}
        </div>
      )}
      {command.length > 0 && <CodeBlock label="Lakebridge Switch command" value={command.join(" ")} />}
      {switchRun && (
        <div className="mt-3 space-y-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <DetailItem label="Switch return code" value={textValue(switchRun, "return_code") ?? "unknown"} />
            {textValue(switchRun, "stdout") && (
              <DetailItem label="Switch stdout" value={textValue(switchRun, "stdout") ?? ""} />
            )}
            {textValue(switchRun, "stderr") && (
              <DetailItem label="Switch stderr" value={textValue(switchRun, "stderr") ?? ""} />
            )}
          </div>
          {stages.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Switch stages
              </div>
              <div className="space-y-2">
                {stages.map((item, index) => {
                  const stage = asRecord(item);
                  if (!stage) return null;
                  return (
                    <SwitchStageRow
                      key={`${textValue(stage, "stage") ?? "stage"}:${index}`}
                      stage={stage}
                    />
                  );
                })}
              </div>
            </div>
          )}
          {generatedFiles.length > 0 && (
            <DetailItem label="Generated files" value={generatedFiles.join(", ")} />
          )}
          {convertedArtifactPreview && (
            <CodeBlock
              label={`Converted artifact${convertedArtifactName ? `: ${convertedArtifactName}` : ""}`}
              value={convertedArtifactPreview}
            />
          )}
        </div>
      )}
    </div>
  );
}

function SwitchStageRow({ stage }: { stage: Record<string, unknown> }) {
  const status = switchStageStatus(stage);
  const runPageUrl = textValue(stage, "run_page_url");
  return (
    <div className="rounded border border-border bg-background/70 p-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-foreground/90">
          {textValue(stage, "stage") ?? "switch_stage"}
        </span>
        <StatusPill status={status} compact />
      </div>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <DetailItem label="Return code" value={textValue(stage, "return_code") ?? "unknown"} />
        {textValue(stage, "run_id") && (
          <DetailItem label="Databricks run id" value={textValue(stage, "run_id") ?? ""} />
        )}
        {textValue(stage, "state") && (
          <DetailItem label="Remote state" value={textValue(stage, "state") ?? ""} />
        )}
        {textValue(stage, "result_state") && (
          <DetailItem label="Remote result" value={textValue(stage, "result_state") ?? ""} />
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
      {textValue(stage, "stderr") && (
        <CodeBlock label="Stage stderr" value={textValue(stage, "stderr") ?? ""} />
      )}
    </div>
  );
}

function switchStageStatus(stage: Record<string, unknown>) {
  const returnCode = textValue(stage, "return_code");
  const state = textValue(stage, "state") ?? textValue(stage, "last_state");
  const resultState = textValue(stage, "result_state");
  if (returnCode === "0" && (!resultState || resultState === "SUCCESS")) return "completed";
  if (state && !["TERMINATED", "SKIPPED", "INTERNAL_ERROR"].includes(state)) return "running";
  return "failed";
}

function MigrationArtifactDetails({
  artifact,
}: {
  artifact: Record<string, unknown>;
}) {
  const compatibility = asRecord(artifact.compatibility_report);
  const paths = asRecord(artifact.artifact_paths);
  const lineage = Array.isArray(artifact.lineage) ? artifact.lineage : [];
  const sourceSql = textValue(artifact, "source_sql");
  const rawSql = textValue(artifact, "raw_databricks_sql");
  const validatedSql = textValue(artifact, "remediated_databricks_sql");
  const rawValidationStatus = textValue(compatibility, "raw_validation_status");
  const validationStatus = textValue(compatibility, "remediation_status");
  const rawPath = textValue(paths, "raw_databricks_sql");
  const validatedPath = textValue(paths, "remediated_databricks_sql");

  return (
    <div className="mt-3 rounded-lg border border-primary/20 bg-primary/5 p-3">
      <div className="mb-2 text-xs font-semibold">Migration Artifact Bundle</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <DetailItem label="Source dialect" value={textValue(artifact, "source_dialect") ?? "unknown"} />
        <DetailItem label="Target dialect" value={textValue(artifact, "target_dialect") ?? "databricks"} />
        {rawValidationStatus && <DetailItem label="Switch validation" value={rawValidationStatus} />}
        {validationStatus && <DetailItem label="Validation result" value={validationStatus} />}
        {rawPath && <DetailItem label="Lakebridge Switch output artifact" value={rawPath} />}
        {textValue(artifact, "optional_validation_object") && (
          <DetailItem
            label="Optional validation object"
            value={textValue(artifact, "optional_validation_object") ?? ""}
          />
        )}
        {validatedPath && <DetailItem label="Lakebridge validated output artifact" value={validatedPath} />}
      </div>

      {lineage.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            Lineage extracted
          </div>
          <div className="grid gap-1 sm:grid-cols-2">
            {lineage.slice(0, 6).map((item, index) => {
              const edge = asRecord(item);
              return (
                <div
                  key={`${textValue(edge, "source") ?? index}:${textValue(edge, "target") ?? ""}`}
                  className="rounded bg-muted/30 px-2 py-1 font-mono text-[10px] text-muted-foreground"
                >
                  {textValue(edge, "source") ?? "source"} -&gt; {textValue(edge, "target") ?? "target"}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {sourceSql && <CodeBlock label="Source SQL" value={sourceSql} />}
      {rawSql && <CodeBlock label="Lakebridge Switch Output Artifact" value={rawSql} />}
      {validatedSql && <CodeBlock label="Lakebridge Validated Databricks SQL" value={validatedSql} />}
      {textValue(compatibility, "raw_validation_error") && (
        <div className="mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-muted-foreground">
          {textValue(compatibility, "raw_validation_error")}
        </div>
      )}
    </div>
  );
}

function MigrationLivePreflightDetails({
  preflight,
}: {
  preflight: Record<string, unknown>;
}) {
  const missing = Array.isArray(preflight.missing)
    ? preflight.missing.map((item) => String(item)).filter(Boolean)
    : [];
  return (
    <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
      <div className="mb-2 text-xs font-semibold">Live SQL Transpile Preflight</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <DetailItem label="Source SQL" value={textValue(preflight, "source_sql") ?? "unknown"} />
        <DetailItem label="Source dialect" value={textValue(preflight, "source_dialect") ?? "unknown"} />
        <DetailItem label="Target dialect" value={textValue(preflight, "target_dialect") ?? "databricks"} />
        <DetailItem label="Live runner" value={textValue(preflight, "live_runner") ?? "unknown"} />
        {textValue(preflight, "source_path") && (
          <DetailItem label="Source path" value={textValue(preflight, "source_path") ?? ""} />
        )}
      </div>
      {textValue(preflight, "message") && (
        <div className="mt-3 rounded border border-border bg-background/50 p-2 text-xs text-muted-foreground">
          {textValue(preflight, "message")}
        </div>
      )}
      {missing.length > 0 && (
        <div className="mt-3 text-xs text-muted-foreground">
          Missing before live Lakebridge Switch SQL transpilation: {missing.join(", ")}
        </div>
      )}
    </div>
  );
}

function LakebridgeSqlRunDetails({ run }: { run: Record<string, unknown> }) {
  const generatedFiles = Array.isArray(run.generated_files)
    ? run.generated_files.map((item) => String(item)).filter(Boolean)
    : [];
  return (
    <div className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3">
      <div className="mb-2 text-xs font-semibold">Lakebridge Switch SQL Run</div>
      <div className="grid gap-2 sm:grid-cols-2">
        <DetailItem label="Return code" value={textValue(run, "return_code") ?? "unknown"} />
        {textValue(run, "output_volume_file") && (
          <DetailItem label="Output Volume file" value={textValue(run, "output_volume_file") ?? ""} />
        )}
        {textValue(run, "output_folder") && (
          <DetailItem label="Local output folder" value={textValue(run, "output_folder") ?? ""} />
        )}
      </div>
      {generatedFiles.length > 0 && (
        <div className="mt-3 text-xs text-muted-foreground">
          Generated files: {generatedFiles.join(", ")}
        </div>
      )}
    </div>
  );
}

function CodeBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-3">
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <pre className="max-h-44 overflow-auto rounded border border-border bg-background/70 p-2 text-[10px] leading-relaxed text-foreground/90">
        {value}
      </pre>
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 break-all font-mono text-[10px] text-foreground/90">
        {value}
      </div>
    </div>
  );
}

function ArtifactPlanCard({ plan }: { plan?: UsecasePayload["artifact_plan"] }) {
  return (
    <div className="rounded-xl border border-border bg-background/70 p-4">
      <div className="mb-3 text-sm font-medium">Build Plan</div>
      {plan?.steps?.length ? (
        <div className="space-y-2">
          {plan.steps.slice(0, 5).map((step, index) => (
            <div
              key={`${step.step_id ?? index}:${step.title ?? ""}`}
              className="rounded border border-border bg-muted/20 p-2 text-xs"
            >
              <div className="font-medium">{step.title ?? step.step_id}</div>
              <div className="mt-1 text-muted-foreground">
                {step.tool_family ?? "Unassigned family"}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No build plan"
          message="Prepare the usecase build plan before execution."
        />
      )}
    </div>
  );
}

function ProofEvidenceCard({ proofs }: { proofs: ToolProof[] }) {
  return (
    <div className="rounded-xl border border-border bg-background/70 p-4">
      <div className="mb-3 text-sm font-medium">Latest Proofs</div>
      <div className="space-y-2">
        {FAMILIES.map((family) => {
          const proof = proofs.find((item) => item.family === family);
          return (
            <div
              key={family}
              className="flex items-center justify-between gap-2 rounded border border-border bg-muted/20 p-2"
            >
              <span className="text-xs font-medium">{familyDisplayLabel(family)}</span>
              <StatusPill status={proof?.status ?? "not_run"} compact />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatusPill({ status, compact = false }: { status: string; compact?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 font-medium",
        compact ? "text-[10px]" : "text-xs",
        statusClass(status),
      )}
    >
      {status.replaceAll("_", " ")}
    </span>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (
    [
      "completed",
      "execution_proven",
      "training_execution_proven",
      "transpilation_proven",
      "transpilation_completed",
      "code_conversion_completed",
      "code_conversion_submitted",
    ].includes(status)
  ) {
    return <CheckCircle2 className="h-4 w-4 text-emerald-400" aria-hidden="true" />;
  }
  if (["queued", "running"].includes(status)) {
    return <Clock3 className="h-4 w-4 text-sky-400" aria-hidden="true" />;
  }
  return <Activity className="h-4 w-4 text-amber-400" aria-hidden="true" />;
}

function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border p-4 text-center">
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-1 text-xs text-muted-foreground">{message}</div>
    </div>
  );
}

function latestExecutionByFamily(executions: ExecutionRun[]) {
  return executions.reduce<Partial<Record<Family, ExecutionRun>>>((acc, run) => {
    if (!FAMILIES.includes(run.family as Family)) return acc;
    const family = run.family as Family;
    if (!acc[family] || run.created_at_ms > acc[family].created_at_ms) {
      acc[family] = run;
    }
    return acc;
  }, {});
}

function familySkillLabel(family: Family) {
  if (family === "SQL") return "skill:delta.sql-transform";
  if (family === "PySpark") return "skill:delta.pyspark-transform";
  if (family === "ML") return "skill:ml.train-evaluate-register";
  if (family === "Code Convert") return "skill:migration.lakebridge-code-convert";
  return "skill:migration.lakebridge-sql-transpile";
}

function familyDisplayLabel(family: Family) {
  if (family === "Migration") return "SQL Transpile";
  return family;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function textValue(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  if (value === null || value === undefined || value === "") return undefined;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return undefined;
}

function booleanValue(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "boolean" ? value : undefined;
}

function statusClass(status: string) {
  if (
    [
      "completed",
      "execution_proven",
      "training_execution_proven",
      "transpilation_proven",
      "transpilation_completed",
      "code_conversion_completed",
      "code_conversion_submitted",
    ].includes(status)
  ) {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  }
  if (["queued", "running"].includes(status)) {
    return "border-sky-500/30 bg-sky-500/10 text-sky-300";
  }
  if (
    [
      "failed",
      "blocked",
      "execution_blocked",
      "transpilation_failed",
      "transpilation_validation_failed",
      "live_transpile_blocked",
      "code_conversion_blocked",
      "code_conversion_failed",
      "execution_failed",
    ].includes(status)
  ) {
    return "border-destructive/30 bg-destructive/10 text-destructive";
  }
  return "border-border bg-muted/40 text-muted-foreground";
}

function dotClass(status: string) {
  if (
    [
      "completed",
      "execution_proven",
      "training_execution_proven",
      "transpilation_proven",
      "transpilation_completed",
      "code_conversion_completed",
      "code_conversion_submitted",
    ].includes(status)
  ) {
    return "bg-emerald-400";
  }
  if (["queued", "running"].includes(status)) {
    return "bg-sky-400";
  }
  if (
    [
      "failed",
      "blocked",
      "transpilation_failed",
      "transpilation_validation_failed",
      "live_transpile_blocked",
      "code_conversion_blocked",
      "code_conversion_failed",
      "skipped",
    ].includes(status)
  ) {
    return "bg-destructive";
  }
  return "bg-muted-foreground";
}

function formatTime(value?: number) {
  if (!value) return "Not updated yet";
  return new Date(value).toLocaleTimeString();
}
