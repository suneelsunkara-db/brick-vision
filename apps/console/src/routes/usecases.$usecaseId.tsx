import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  CheckCircle2,
  FlaskConical,
  GitBranch,
  Hammer,
  Rocket,
  Sparkles,
  Target,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/usecases/$usecaseId")({
  component: UsecaseDetailPage,
});

interface UsecaseRecord {
  usecase_id: string;
  candidate_id: string;
  status: string;
  title: string;
  outcome: string;
  persona: string;
  value_hypothesis: string;
  readiness: string;
  active_snapshot_id?: string;
  candidate?: {
    evidence_summary?: string;
    proposal_kind?: string;
    suggested_strategy?: string;
    why_proposed?: string;
    detected_entities?: string[];
    build_paths?: string[];
    evidence_refs?: Array<{ kind: string; ref: string }>;
    evidence_tables?: Array<{
      table_name: string;
      table_ref: string;
      row_count: number;
      column_count: number;
      profiled_column_count: number;
      candidate_key_columns?: string[];
      null_risk_columns?: string[];
    }>;
    required_skill_families?: Array<{ family: string; status: string }>;
    missing_inputs?: string[];
    starter_artifacts?: Array<{
      kind: string;
      template_id: string;
      title?: string;
      status?: string;
      target_ref: string;
    }>;
  };
  inputs?: {
    acceptance_criteria?: string[];
    missing_input_values?: Record<string, string>;
    created_at_ms?: number;
  };
  strategy?: {
    strategy_id: string;
    strategy_kind: string;
    rationale: string;
    required_skill_families: string[];
    created_at_ms?: number;
  } | null;
  artifact_plan?: ArtifactPlan | null;
  artifact_validation?: ArtifactValidation | null;
  evaluation?: UsecaseEvaluation | null;
  created_at_ms?: number;
  updated_at_ms?: number;
  message?: string;
}

interface SkillResolution {
  status: string;
  message?: string;
  strategy_kind?: string;
  missing_count?: number;
  next_action?: string;
  skills: Array<{
    family: string;
    status: string;
    reason: string;
    skill_id: string;
    next_action: string;
  }>;
}

interface SkillInputRequirements {
  status: string;
  next_action?: string;
  missing_binding_count?: number;
  requirements: Array<{
    family: string;
    skill_id: string;
    status: string;
    required: boolean;
    binding_status: string;
    binding?: {
      inputs?: Record<string, string>;
      created_at_ms?: number;
    };
    fields: Array<{
      name: string;
      type: string;
      required: boolean;
      description?: string;
    }>;
  }>;
}

interface ArtifactPlan {
  artifact_plan_id?: string;
  status: string;
  message?: string;
  strategy_kind?: string;
  next_action?: string;
  steps?: Array<{
    step_id: string;
    family: string;
    skill_id: string;
    artifact_kind: string;
    status: string;
    bound_inputs?: Record<string, string>;
  }>;
}

interface ArtifactValidation {
  validation_id?: string;
  status: string;
  message?: string;
  next_action?: string;
  findings?: Array<{
    severity: string;
    code: string;
    message: string;
  }>;
}

interface UsecaseEvaluation {
  evaluation_id?: string;
  status: string;
  decision: string;
  next_action?: string;
  blockers?: Array<{
    code: string;
    message: string;
  }>;
}

interface RuntimeFinding {
  severity: string;
  code: string;
  message: string;
}

interface RuntimeSkillStatus {
  skill_id: string;
  status: string;
  declared_tools?: string[];
  findings?: RuntimeFinding[];
}

interface ToolProofsPayload {
  status: string;
  next_action?: string;
  proofs: Array<{
    family: string;
    proof_id?: string;
    status: string;
    skill_id: string;
    result?: {
      status?: string;
      executed?: boolean;
      object_name?: string;
      message?: string;
      error_kind?: string;
      proof_kind?: string;
      data_pipeline_run?: Record<string, unknown>;
      statement_execution_plan?: Record<string, unknown>;
      pyspark_task_plan?: Record<string, unknown>;
      jobs_submit_plan?: Record<string, unknown>;
      problem_selection?: Record<string, unknown>;
      feature_readiness?: Record<string, unknown>;
      strategy_plan?: Record<string, unknown>;
      model_family?: Record<string, unknown>;
      backend_probe?: Record<string, unknown>;
      backend_selection?: Record<string, unknown>;
      training_artifact_plan?: Record<string, unknown>;
      training_task_plan?: Record<string, unknown>;
      api_plan_binding?: Record<string, unknown>;
      training_gate?: Record<string, unknown>;
      training_result?: Record<string, unknown> | null;
      runtime?: {
        family: string;
        status: string;
        next_action?: string;
        skills?: RuntimeSkillStatus[];
        findings?: RuntimeFinding[];
      };
    };
    next_action?: string;
  }>;
}

const STRATEGY_OPTIONS = [
  {
    kind: "sql_only",
    label: "SQL-only",
    body: "Use SQL views/tables for the first implementation artifact.",
  },
  {
    kind: "pyspark_pipeline",
    label: "PySpark pipeline",
    body: "Move beyond SQL into notebook/job based transformation and profiling.",
  },
  {
    kind: "ml_workflow",
    label: "ML workflow",
    body: "Add feature preparation, model training, quality checks, and serving path.",
  },
  {
    kind: "ai_agent",
    label: "AI agent",
    body: "Add retrieval or agent capability, test set, and deployment target.",
  },
  {
    kind: "migration_assessment",
    label: "Migration assessment",
    body: "Use source files, migration analysis, remediation, and reconciliation.",
  },
  {
    kind: "composite",
    label: "Composite",
    body: "Use multiple skill families when the usecase spans SQL, PySpark, ML, AI, or migration.",
  },
] as const;

const DETAIL_STEPS = [
  { name: "Proposal", Icon: Target },
  { name: "Evidence", Icon: Sparkles },
  { name: "Build path", Icon: GitBranch },
  { name: "Capabilities", Icon: Brain },
  { name: "Build plan", Icon: Hammer },
  { name: "Quality check", Icon: CheckCircle2 },
  { name: "Review", Icon: FlaskConical },
  { name: "Deploy", Icon: Rocket },
] as const;

function UsecaseDetailPage() {
  const { usecaseId } = Route.useParams();
  const queryClient = useQueryClient();
  const [acceptanceText, setAcceptanceText] = useState("");
  const [missingValues, setMissingValues] = useState<Record<string, string>>({});
  const [strategyKind, setStrategyKind] = useState("sql_only");
  const [strategyRationale, setStrategyRationale] = useState("");
  const [skillInputValues, setSkillInputValues] = useState<
    Record<string, Record<string, string>>
  >({});
  const { data, isLoading } = useQuery({
    queryKey: ["usecases", usecaseId],
    queryFn: () =>
      fetchJson<UsecaseRecord>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}`,
      ),
    staleTime: 30_000,
  });
  const { data: skillResolution } = useQuery({
    queryKey: ["usecases", usecaseId, "skills"],
    queryFn: () =>
      fetchJson<SkillResolution>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/skills`,
      ),
    staleTime: 30_000,
  });
  const { data: skillInputRequirements } = useQuery({
    queryKey: ["usecases", usecaseId, "skill-inputs"],
    queryFn: () =>
      fetchJson<SkillInputRequirements>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/skill-inputs`,
      ),
    staleTime: 30_000,
  });
  const { data: toolProofs } = useQuery({
    queryKey: ["usecases", usecaseId, "tool-proofs"],
    queryFn: () =>
      fetchJson<ToolProofsPayload>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/tool-proofs`,
      ),
    staleTime: 30_000,
  });
  const saveInputsMutation = useMutation({
    mutationFn: () =>
      fetchJson<UsecaseRecord>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/inputs`,
        {
          method: "POST",
          body: JSON.stringify({
            acceptance_criteria: acceptanceText
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean),
            missing_input_values: missingValues,
          }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["usecases", usecaseId] });
    },
  });
  const saveStrategyMutation = useMutation({
    mutationFn: () =>
      fetchJson<UsecaseRecord>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/strategy`,
        {
          method: "POST",
          body: JSON.stringify({
            strategy_kind: strategyKind,
            rationale: strategyRationale,
          }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["usecases", usecaseId] });
    },
  });
  const saveSkillInputsMutation = useMutation({
    mutationFn: (requirement: SkillInputRequirements["requirements"][number]) =>
      fetchJson<SkillInputRequirements>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/skill-inputs`,
        {
          method: "POST",
          body: JSON.stringify({
            family: requirement.family,
            skill_id: requirement.skill_id,
            inputs: skillInputValues[requirement.family] ?? {},
          }),
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["usecases", usecaseId, "skill-inputs"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["usecases", usecaseId, "skills"],
      });
    },
  });
  const generateArtifactPlanMutation = useMutation({
    mutationFn: () =>
      fetchJson<ArtifactPlan>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/artifact-plan`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["usecases", usecaseId] });
    },
  });
  const validateArtifactPlanMutation = useMutation({
    mutationFn: () =>
      fetchJson<ArtifactValidation>(
        `/api/knowledge/usecases/${encodeURIComponent(
          usecaseId,
        )}/artifact-plan/validate`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["usecases", usecaseId] });
    },
  });
  const evaluateMutation = useMutation({
    mutationFn: () =>
      fetchJson<UsecaseEvaluation>(
        `/api/knowledge/usecases/${encodeURIComponent(usecaseId)}/evaluation`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["usecases", usecaseId] });
    },
  });
  const runToolProofMutation = useMutation({
    mutationFn: (family: string) =>
      fetchJson<ToolProofsPayload["proofs"][number]>(
        `/api/knowledge/usecases/${encodeURIComponent(
          usecaseId,
        )}/tool-proofs/${encodeURIComponent(family)}`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["usecases", usecaseId, "tool-proofs"],
      });
    },
  });

  useEffect(() => {
    if (!data || data.status === "not_found") return;
    setAcceptanceText((data.inputs?.acceptance_criteria ?? []).join("\n"));
    setMissingValues(
      Object.fromEntries(
        Object.entries(data.inputs?.missing_input_values ?? {}).map(
          ([key, value]) => [key, String(value)],
        ),
      ),
    );
    if (data.strategy?.strategy_kind) {
      setStrategyKind(data.strategy.strategy_kind);
      setStrategyRationale(data.strategy.rationale ?? "");
    }
  }, [data]);

  useEffect(() => {
    if (!skillInputRequirements) return;
    setSkillInputValues(
      Object.fromEntries(
        skillInputRequirements.requirements.map((requirement) => [
          requirement.family,
          Object.fromEntries(
            Object.entries(requirement.binding?.inputs ?? {}).map(
              ([key, value]) => [key, String(value)],
            ),
          ),
        ]),
      ),
    );
  }, [skillInputRequirements]);

  if (isLoading) return <PanelSkeleton />;

  if (!data || data.status === "not_found") {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 p-6">
        <Button asChild variant="outline">
          <Link to="/usecases">Back to Usecases</Link>
        </Button>
        <Card>
          <CardHeader>
            <CardTitle>Usecase not found</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {data?.message ?? "The selected usecase record does not exist."}
          </CardContent>
        </Card>
      </div>
    );
  }

  const candidate = data.candidate ?? {};
  const evidenceRefs = candidate.evidence_refs ?? [];
  const tableEvidence = evidenceRefs.filter((ref) => ref.kind === "table");
  const evidenceTables = candidate.evidence_tables ?? [];
  const detectedEntities = candidate.detected_entities ?? [];
  const buildPaths = candidate.build_paths ?? [];
  const missingInputs = candidate.missing_inputs ?? [];
  const savedCriteria = data.inputs?.acceptance_criteria ?? [];
  const savedValues = data.inputs?.missing_input_values ?? {};
  const hasInputs =
    savedCriteria.length > 0 ||
    Object.values(savedValues).some((value) => String(value).trim());
  const selectedStrategy = data.strategy;
  const artifactPlan = data.artifact_plan;
  const artifactValidation =
    validateArtifactPlanMutation.data ?? data.artifact_validation;
  const evaluation = evaluateMutation.data ?? data.evaluation;
  const canPlanArtifacts =
    skillInputRequirements?.status === "ready_to_plan_artifacts";
  const canValidateArtifacts = Boolean(artifactPlan?.steps?.length);
  const canEvaluate = artifactValidation?.status === "passed";
  const skillsResolved = skillResolution?.status === "ready";

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/usecases">Back to Opportunities</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/execution-monitor/$usecaseId" params={{ usecaseId }}>
              Open Linked Runs
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/migrations">Migration Workflows</Link>
          </Button>
        </div>
      </div>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{data.title}</h1>
          <StatusBadge status={data.status} />
          <StatusBadge status={data.readiness} muted />
        </div>
        <p className="max-w-4xl text-sm text-muted-foreground">
          {data.outcome}
        </p>
        <p className="max-w-4xl text-xs text-muted-foreground">
          This record is the KG-derived opportunity: it captures why the work is
          valuable and which evidence supports it. Execution belongs to linked
          skill or workflow runs.
        </p>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span className="rounded bg-muted/40 px-2 py-1">{data.persona}</span>
          {candidate.proposal_kind && (
            <span className="rounded bg-muted/40 px-2 py-1">
              {candidate.proposal_kind}
            </span>
          )}
          {candidate.suggested_strategy && (
            <span className="rounded bg-muted/40 px-2 py-1">
              Strategy: {candidate.suggested_strategy}
            </span>
          )}
          {data.active_snapshot_id && (
            <span className="rounded bg-muted/40 px-2 py-1 font-mono">
              {data.active_snapshot_id}
            </span>
          )}
        </div>
      </header>

      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle className="text-base">Why BrickVision Proposed This</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <p>{candidate.why_proposed ?? data.value_hypothesis}</p>
          {candidate.evidence_summary && (
            <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs">
              {candidate.evidence_summary}
            </div>
          )}
          {tableEvidence.length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Relevant tables
              </div>
              <div className="flex flex-wrap gap-2">
                {tableEvidence.map((ref) => (
                  <span
                    key={ref.ref}
                    className="rounded bg-primary/10 px-2 py-1 font-mono text-[10px] text-primary"
                  >
                    {ref.ref}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="grid gap-3 lg:grid-cols-2">
            {detectedEntities.length > 0 && (
              <div className="rounded-lg border border-border bg-muted/10 p-3">
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Detected data entities
                </div>
                <div className="flex flex-wrap gap-2">
                  {detectedEntities.map((entity) => (
                    <span
                      key={entity}
                      className="rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground"
                    >
                      {entity}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {buildPaths.length > 0 && (
              <div className="rounded-lg border border-border bg-muted/10 p-3">
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Candidate build paths
                </div>
                <div className="space-y-1 text-xs">
                  {buildPaths.map((path) => (
                    <div key={path}>- {path}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
          {evidenceTables.length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Table evidence
              </div>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {evidenceTables.map((table) => (
                  <div
                    key={table.table_ref}
                    className="rounded-lg border border-border bg-background p-3"
                  >
                    <div className="font-mono text-xs text-foreground">
                      {table.table_name}
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-2 text-[10px]">
                      <Metric label="Rows" value={formatNumber(table.row_count)} />
                      <Metric label="Columns" value={String(table.column_count)} />
                      <Metric
                        label="Profiled"
                        value={String(table.profiled_column_count)}
                      />
                    </div>
                    {(table.candidate_key_columns?.length ?? 0) > 0 && (
                      <div className="mt-2 text-[10px] text-muted-foreground">
                        Grain/key: {table.candidate_key_columns?.join(", ")}
                      </div>
                    )}
                    {(table.null_risk_columns?.length ?? 0) > 0 && (
                      <div className="mt-1 text-[10px] text-amber-300">
                        Null risk: {table.null_risk_columns?.slice(0, 4).join(", ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200">
            This is still a proposed usecase. BrickVision will not build anything
            until the required business details and success criteria are reviewed.
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Build Progress</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {DETAIL_STEPS.map(({ name, Icon }) => {
              const state = stepState(name, {
                hasInputs,
                selectedStrategy,
                skillsResolved,
                artifactPlan,
                artifactValidation,
                evaluation,
              });
              return (
              <div
                key={name}
                className={cn(
                  "rounded-lg border p-3",
                  state === "started"
                    ? "border-primary/30 bg-primary/10"
                    : state === "complete"
                      ? "border-emerald-500/30 bg-emerald-500/10"
                    : "border-border bg-muted/10",
                )}
              >
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Icon className="h-4 w-4 text-primary" />
                  {name}
                </div>
                <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {state}
                </div>
              </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Proposed Outcome</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <p>{data.value_hypothesis}</p>
            {savedCriteria.length > 0 ? (
              <div className="space-y-1 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs">
                <div className="font-medium text-emerald-300">
                  Acceptance criteria saved
                </div>
                {savedCriteria.map((criterion) => (
                  <div key={criterion}>- {criterion}</div>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs">
                Success criteria are not filled yet. Add what a good outcome
                must prove before BrickVision prepares the build plan.
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Details Needed</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {missingInputs.map((input) => (
              <span
                key={input}
                className={cn(
                  "rounded px-2 py-1 text-xs",
                  savedValues[input]
                    ? "bg-emerald-500/10 text-emerald-400"
                    : "bg-muted/50 text-muted-foreground",
                )}
              >
                {input}
                {savedValues[input] ? ": saved" : ""}
              </span>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add Required Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="block space-y-2">
            <span className="text-sm font-medium">Success Criteria</span>
            <textarea
              value={acceptanceText}
              onChange={(event) => setAcceptanceText(event.target.value)}
              placeholder="One criterion per line, e.g. No table has more than 1% null customer IDs"
              className="min-h-28 w-full rounded-lg border border-border bg-background p-3 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </label>

          {missingInputs.length > 0 && (
            <div className="space-y-3">
              <div className="text-sm font-medium">Details Needed</div>
              <div className="grid gap-3 md:grid-cols-2">
                {missingInputs.map((input) => (
                  <label key={input} className="block space-y-1">
                    <span className="text-xs text-muted-foreground">{input}</span>
                    <input
                      value={missingValues[input] ?? ""}
                      onChange={(event) =>
                        setMissingValues((current) => ({
                          ...current,
                          [input]: event.target.value,
                        }))
                      }
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center justify-between gap-3">
            <div className="text-xs text-muted-foreground">
              These details are saved with the proposal so the build can be
              reviewed and replayed later.
            </div>
            <Button
              disabled={saveInputsMutation.isPending}
              onClick={() => saveInputsMutation.mutate()}
            >
              {saveInputsMutation.isPending ? "Saving..." : "Save Required Details"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Choose Build Path</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!hasInputs && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Add the required details before choosing how BrickVision should
              build this usecase.
            </div>
          )}

          {selectedStrategy && (
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-muted-foreground">
              <div className="font-medium text-emerald-300">
                Selected build path: {selectedStrategy.strategy_kind}
              </div>
              <div className="mt-1">{selectedStrategy.rationale}</div>
              <div className="mt-2 flex flex-wrap gap-1">
                {selectedStrategy.required_skill_families.map((family) => (
                  <span
                    key={family}
                    className="rounded bg-emerald-500/10 px-2 py-1 text-emerald-400"
                  >
                    {family}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {STRATEGY_OPTIONS.map((option) => (
              <button
                key={option.kind}
                type="button"
                disabled={!hasInputs}
                onClick={() => setStrategyKind(option.kind)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                  strategyKind === option.kind
                    ? "border-primary/50 bg-primary/10"
                    : "border-border bg-muted/10 hover:bg-muted/20",
                )}
              >
                <div className="text-sm font-medium">{option.label}</div>
                <p className="mt-2 text-xs text-muted-foreground">{option.body}</p>
              </button>
            ))}
          </div>

          <label className="block space-y-2">
            <span className="text-sm font-medium">Why this build path?</span>
            <textarea
              value={strategyRationale}
              disabled={!hasInputs}
              onChange={(event) => setStrategyRationale(event.target.value)}
              placeholder="Why is this the right first build path for this usecase?"
              className="min-h-24 w-full rounded-lg border border-border bg-background p-3 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
            />
          </label>

          <div className="flex justify-end">
            <Button
              disabled={!hasInputs || saveStrategyMutation.isPending}
              onClick={() => saveStrategyMutation.mutate()}
            >
              {saveStrategyMutation.isPending ? "Saving..." : "Save Build Path"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Evidence</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {candidate.evidence_summary}
            </p>
            <div className="flex flex-wrap gap-1">
              {(candidate.evidence_refs ?? []).map((ref) => (
                <span
                  key={`${ref.kind}:${ref.ref}`}
                  className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground"
                >
                  {ref.kind}:{ref.ref}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Build Capabilities</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {skillResolution?.status === "strategy_required" && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
                {skillResolution.message}
              </div>
            )}

            {skillResolution?.skills.map((skill) => (
              <div
                key={skill.family}
                className="rounded-lg border border-border bg-muted/10 p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium">{skill.family}</div>
                  <StatusBadge
                    status={skill.status}
                    muted={!isResolvedSkillStatus(skill.status)}
                  />
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  {skill.reason}
                </p>
                {skill.skill_id && (
                  <div className="mt-2 font-mono text-[10px] text-muted-foreground">
                    {skill.skill_id}
                  </div>
                )}
                <div className="mt-3 flex items-center justify-between gap-3">
                  <div className="text-xs text-muted-foreground">
                    {skill.next_action}
                  </div>
                  {!isResolvedSkillStatus(skill.status) && (
                    <Button disabled size="sm" variant="outline">
                      {skill.status === "needs_inputs"
                        ? "Add Details"
                        : "Open Skill Catalog"}
                    </Button>
                  )}
                </div>
              </div>
            ))}

            {skillResolution?.next_action && (
              <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
                {skillResolution.next_action}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {skillInputRequirements &&
        skillInputRequirements.requirements.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Required Build Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
                {skillInputRequirements.next_action}
              </div>

              {skillInputRequirements.requirements.map((requirement) => (
                <div
                  key={requirement.family}
                  className="rounded-lg border border-border p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-medium">{requirement.family}</div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                        {requirement.skill_id || "no concrete skill selected"}
                      </div>
                    </div>
                    <StatusBadge
                      status={requirement.binding_status}
                      muted={requirement.binding_status !== "bound"}
                    />
                  </div>

                  {requirement.fields.length === 0 ? (
                    <div className="mt-3 text-xs text-muted-foreground">
                      This family does not have bindable inputs in the current
                      planner yet.
                    </div>
                  ) : (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      {requirement.fields.map((field) => (
                        <label key={field.name} className="block space-y-1">
                          <span className="text-xs text-muted-foreground">
                            {field.name} · {field.type}
                            {field.required ? " · required" : ""}
                          </span>
                          {(field.description || skillInputHint(field.name)) && (
                            <p className="text-[10px] text-muted-foreground">
                              {field.description || skillInputHint(field.name)}
                            </p>
                          )}
                          {usesMultilineInput(field) ? (
                            <textarea
                              value={
                                skillInputValues[requirement.family]?.[
                                  field.name
                                ] ?? ""
                              }
                              onChange={(event) =>
                                setSkillInputValue(
                                  setSkillInputValues,
                                  requirement.family,
                                  field.name,
                                  event.target.value,
                                )
                              }
                              placeholder={skillInputPlaceholder(field.name, field.type)}
                              className="min-h-28 w-full rounded-lg border border-border bg-background p-2 font-mono text-xs shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                            />
                          ) : (
                            <input
                              value={
                                skillInputValues[requirement.family]?.[
                                  field.name
                                ] ?? ""
                              }
                              onChange={(event) =>
                                setSkillInputValue(
                                  setSkillInputValues,
                                  requirement.family,
                                  field.name,
                                  event.target.value,
                                )
                              }
                              placeholder={skillInputPlaceholder(field.name, field.type)}
                              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                            />
                          )}
                        </label>
                      ))}
                    </div>
                  )}

                  <div className="mt-3 flex justify-end">
                    <Button
                      size="sm"
                      disabled={saveSkillInputsMutation.isPending}
                      onClick={() => saveSkillInputsMutation.mutate(requirement)}
                    >
                      {saveSkillInputsMutation.isPending
                        ? "Saving..."
                        : `Save ${requirement.family} Details`}
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Build Plan</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!canPlanArtifacts && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Add required details before BrickVision prepares the build plan.
            </div>
          )}

          {generateArtifactPlanMutation.data?.status ===
            "blocked_missing_skill_inputs" && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              {generateArtifactPlanMutation.data.message}
            </div>
          )}

          {artifactPlan?.steps && artifactPlan.steps.length > 0 ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={artifactPlan.status} />
                {artifactPlan.strategy_kind && (
                  <StatusBadge status={artifactPlan.strategy_kind} muted />
                )}
              </div>
              {artifactPlan.steps.map((step) => (
                <div
                  key={step.step_id}
                  className="rounded-lg border border-border bg-muted/10 p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium">{step.step_id}</div>
                    <StatusBadge status={step.status} muted />
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                    {step.skill_id} · {step.artifact_kind}
                  </div>
                  {step.bound_inputs && (
                    <div className="mt-3 grid gap-1 text-xs text-muted-foreground md:grid-cols-2">
                      {Object.entries(step.bound_inputs).map(([key, value]) => (
                        <div key={key} className="rounded bg-muted/30 px-2 py-1">
                          <span className="font-medium">{key}</span>: {String(value)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {artifactPlan.next_action && (
                <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
                  {artifactPlan.next_action}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No build plan has been prepared yet.
            </div>
          )}

          <div className="flex justify-end">
            <Button
              disabled={!canPlanArtifacts || generateArtifactPlanMutation.isPending}
              onClick={() => generateArtifactPlanMutation.mutate()}
            >
              {generateArtifactPlanMutation.isPending
                ? "Generating..."
                : "Prepare Build Plan"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Build Readiness</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
            {toolProofs?.next_action ??
              "Check whether BrickVision has the right tools and approved skill paths to build this usecase."}
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {(toolProofs?.proofs ?? []).map((proof) => (
              <div
                key={proof.family}
                className="rounded-lg border border-border bg-muted/10 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium">{proof.family}</div>
                  <StatusBadge
                    status={proof.status}
                    muted={!isProofReady(proof)}
                  />
                </div>
                <div className="mt-2 text-[10px] text-muted-foreground">
                  Technical skill: <span className="font-mono">{proof.skill_id}</span>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  {proofUserMessage(proof)}
                </p>
                {proof.result && <SkillPlanChain proof={proof} />}
                {proof.result?.runtime?.skills && (
                  <div className="mt-3 space-y-2">
                    {proof.result.runtime.skills.map((skill) => (
                      <div
                        key={skill.skill_id}
                        className="rounded border border-border bg-background/40 p-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-[10px] text-muted-foreground">
                            {skill.skill_id}
                          </span>
                          <StatusBadge
                            status={skill.status}
                            muted={skill.status !== "runtime_ready"}
                          />
                        </div>
                        {(skill.declared_tools ?? []).length > 0 && (
                          <div className="mt-2 space-y-1">
                            <div className="text-[10px] text-muted-foreground">
                              Technical tools BrickVision will use:
                            </div>
                            {(skill.declared_tools ?? []).map((tool) => (
                              <div
                                key={tool}
                                className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground"
                              >
                                {tool}
                              </div>
                            ))}
                          </div>
                        )}
                        {(skill.findings ?? []).slice(0, 2).map((finding) => (
                          <div
                            key={`${skill.skill_id}:${finding.code}:${finding.message}`}
                            className="mt-2 rounded bg-amber-500/10 p-2 text-[10px] text-muted-foreground"
                          >
                            <div className="font-medium text-amber-300">
                              Attention needed
                            </div>
                            <div>{finding.message}</div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-3">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={
                      runToolProofMutation.isPending ||
                      !["SQL", "PySpark", "ML", "AI"].includes(proof.family)
                    }
                    onClick={() => runToolProofMutation.mutate(proof.family)}
                  >
                    {runToolProofMutation.isPending
                      ? "Checking..."
                      : proof.family === "SQL"
                        ? "Run SQL Build"
                        : proof.family === "PySpark"
                          ? "Run PySpark Build"
                          : proof.family === "ML"
                            ? "Run ML Build"
                        : `Check ${proof.family} Tools`}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quality Check</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!canValidateArtifacts && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Prepare the build plan before running quality checks.
            </div>
          )}

          {artifactValidation ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  status={artifactValidation.status}
                  muted={artifactValidation.status !== "passed"}
                />
                {artifactValidation.validation_id && (
                  <span className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                    {artifactValidation.validation_id}
                  </span>
                )}
              </div>
              {(artifactValidation.findings ?? []).map((finding) => (
                <div
                  key={`${finding.code}:${finding.message}`}
                  className={cn(
                    "rounded-lg border p-3 text-xs",
                    finding.severity === "blocking"
                      ? "border-destructive/30 bg-destructive/10 text-muted-foreground"
                      : "border-emerald-500/30 bg-emerald-500/10 text-muted-foreground",
                  )}
                >
                  <div className="font-medium">
                    {finding.severity}: {finding.code}
                  </div>
                  <div className="mt-1">{finding.message}</div>
                </div>
              ))}
              {artifactValidation.next_action && (
                <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
                  {artifactValidation.next_action}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No quality check has been run yet.
            </div>
          )}

          <div className="flex justify-end">
            <Button
              disabled={!canValidateArtifacts || validateArtifactPlanMutation.isPending}
              onClick={() => validateArtifactPlanMutation.mutate()}
            >
              {validateArtifactPlanMutation.isPending
                ? "Checking..."
                : "Run Quality Check"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Review Readiness</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!canEvaluate && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Quality checks must pass before BrickVision can mark this ready
              for review.
            </div>
          )}

          {evaluation ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  status={evaluation.decision}
                  muted={evaluation.decision !== "ready_for_execution"}
                />
                {evaluation.evaluation_id && (
                  <span className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                    {evaluation.evaluation_id}
                  </span>
                )}
              </div>
              {(evaluation.blockers ?? []).length > 0 ? (
                (evaluation.blockers ?? []).map((blocker) => (
                  <div
                    key={`${blocker.code}:${blocker.message}`}
                    className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-muted-foreground"
                  >
                    <div className="font-medium">{blocker.code}</div>
                    <div className="mt-1">{blocker.message}</div>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-muted-foreground">
                  No blockers found for the checked build plan.
                </div>
              )}
              {evaluation.next_action && (
                <div className="rounded-lg bg-primary/10 p-3 text-xs text-primary">
                  {evaluation.next_action}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              Readiness has not been checked yet.
            </div>
          )}

          <div className="flex justify-end">
            <Button
              disabled={!canEvaluate || evaluateMutation.isPending}
              onClick={() => evaluateMutation.mutate()}
            >
              {evaluateMutation.isPending ? "Checking..." : "Check Review Readiness"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Possible Outputs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {(candidate.starter_artifacts ?? []).map((artifact) => (
            <div
              key={`${artifact.template_id}:${artifact.target_ref}`}
              className="rounded-lg border border-border p-3 text-sm"
            >
              <div className="font-medium">{artifact.title}</div>
              <div className="mt-1 font-mono text-xs text-muted-foreground">
                {artifact.kind} · {artifact.template_id} · {artifact.target_ref}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border bg-muted/20 p-2">
      <div className="uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 font-medium text-foreground">{value}</div>
    </div>
  );
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function StatusBadge({
  status,
  muted = false,
}: {
  status: string;
  muted?: boolean;
}) {
  return (
    <span
      className={cn(
        "rounded px-2 py-1 text-[10px] font-medium uppercase tracking-wide",
        muted
          ? "bg-muted/50 text-muted-foreground"
          : "bg-primary/10 text-primary",
      )}
    >
      {displayStatus(status)}
    </span>
  );
}

function SkillPlanChain({ proof }: { proof: ToolProofsPayload["proofs"][number] }) {
  const result = proof.result;
  if (!result) return null;
  const entries =
    proof.family === "ML"
      ? [
          ["Problem", result.problem_selection],
          ["Features", result.feature_readiness],
          ["Strategy", result.strategy_plan],
          ["Model family", result.model_family],
          ["Backend probe", result.backend_probe],
          ["Backend selection", result.backend_selection],
          ["Training artifact", result.training_artifact_plan],
          ["Training task", result.training_task_plan],
          ["API plan", result.api_plan_binding],
          ["Training gate", result.training_gate],
          ["Training result", result.training_result ?? undefined],
        ]
      : [
          ["Pipeline run", result.data_pipeline_run],
          ["Statement plan", nestedPlan(result.data_pipeline_run, "statement_execution_plan")],
          ["PySpark task", nestedPlan(result.data_pipeline_run, "pyspark_task_plan")],
          ["Jobs submit", nestedPlan(result.data_pipeline_run, "jobs_submit_plan")],
        ];
  const visible = entries.filter((entry): entry is [string, Record<string, unknown>] =>
    isRecord(entry[1]),
  );
  if (visible.length === 0) return null;
  return (
    <div className="mt-3 space-y-2">
      {visible.map(([label, value]) => (
        <details
          key={`${proof.family}:${label}`}
          className="rounded border border-border bg-background/40 p-2"
        >
          <summary className="cursor-pointer text-[10px] font-medium text-muted-foreground">
            {label}: {planSummary(value)}
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded bg-muted/30 p-2 text-[10px] text-muted-foreground">
            {JSON.stringify(value, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  );
}

function nestedPlan(value: unknown, key: string): Record<string, unknown> | undefined {
  if (!isRecord(value)) return undefined;
  const metadata = value.metadata;
  if (!isRecord(metadata)) return undefined;
  return isRecord(metadata[key]) ? metadata[key] : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function planSummary(value: Record<string, unknown>) {
  for (const key of ["status", "backend_id", "family_id", "problem_type", "execution_success"]) {
    const item = value[key];
    if (typeof item === "string" || typeof item === "boolean" || typeof item === "number") {
      return String(item);
    }
  }
  const selectedBackend = value.selected_backend;
  if (isRecord(selectedBackend) && typeof selectedBackend.backend_id === "string") {
    return selectedBackend.backend_id;
  }
  const selectedFamily = value.selected_model_family;
  if (isRecord(selectedFamily) && typeof selectedFamily.family_id === "string") {
    return selectedFamily.family_id;
  }
  return "details";
}

function displayStatus(status: string) {
  const labels: Record<string, string> = {
    draft: "Proposed",
    draft_inputs_saved: "Details saved",
    draft_strategy_selected: "Build path selected",
    proposed: "Proposed",
    candidate: "Proposed",
    evidence_ready_skill_gaps: "Evidence ready",
    evidence_partial: "Evidence partial",
    not_ready: "Not ready",
    started: "In progress",
    complete: "Done",
    pending: "Not started",
    available: "Ready",
    covered: "Ready",
    needs_inputs: "Needs details",
    needs_skill_builder: "Needs skill",
    needs_partner_skill: "Needs partner skill",
    bound: "Details added",
    unbound: "Needs details",
    ready_to_plan_artifacts: "Ready to plan",
    ready: "Ready",
    resolved_with_gaps: "Needs attention",
    execution_proven: "Build ran",
    training_execution_proven: "Training ran",
    runtime_ready: "Tools available",
    runtime_adapter_missing: "Tools missing",
    tool_adapter_missing: "Tools missing",
    skill_contract_invalid: "Skill issue",
    execution_failed: "Build failed",
    passed: "Passed",
    failed: "Failed",
    ready_for_execution: "Ready to execute",
    blocked_missing_skill_inputs: "Needs details",
    sql_only: "SQL build",
    pyspark_pipeline: "PySpark build",
    ml_workflow: "ML build",
    ai_agent: "AI build",
    migration_assessment: "Migration review",
    composite: "Composite build",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

function isProofReady(proof: ToolProofsPayload["proofs"][number]) {
  if (["SQL", "PySpark", "ML"].includes(proof.family)) {
    return proof.status === "execution_proven" || proof.status === "training_execution_proven";
  }
  return proof.status === "runtime_ready";
}

function proofUserMessage(proof: ToolProofsPayload["proofs"][number]) {
  if (isProofReady(proof) && proof.family === "SQL") {
    return "BrickVision ran this SQL build through the SQL transform and Statement Execution skill chain.";
  }
  if (isProofReady(proof) && proof.family === "PySpark") {
    return "BrickVision ran this PySpark build through transform, task plan, and Jobs submit skills.";
  }
  if (isProofReady(proof) && proof.family === "ML") {
    return "BrickVision ran this ML path through readiness, model-family, backend, Jobs/API, and training skills.";
  }
  if (proof.status === "runtime_ready") {
    return `BrickVision has the ${proof.family} tools available, but this usecase has not run an end-to-end ${proof.family} build yet.`;
  }
  if (proof.family === "AI") {
    return "A proper AI usecase skill is still needed before BrickVision can claim an end-to-end AI build.";
  }
  return `BrickVision still needs the right ${proof.family} tools or details before this path can run end-to-end.`;
}

function stepState(
  name: string,
  state: {
    hasInputs: boolean;
    selectedStrategy: UsecaseRecord["strategy"];
    skillsResolved: boolean;
    artifactPlan: UsecaseRecord["artifact_plan"];
    artifactValidation: UsecaseRecord["artifact_validation"];
    evaluation: UsecaseRecord["evaluation"];
  },
) {
  if (name === "Proposal") return state.hasInputs ? "complete" : "started";
  if (name === "Evidence") return "complete";
  if (name === "Build path") return state.selectedStrategy ? "complete" : "started";
  if (name === "Capabilities") {
    if (state.skillsResolved) return "complete";
    return state.selectedStrategy ? "started" : "pending";
  }
  if (name === "Build plan") {
    if (state.artifactPlan?.steps?.length) return "complete";
    return state.skillsResolved ? "started" : "pending";
  }
  if (name === "Quality check") {
    if (state.artifactValidation?.status === "passed") return "complete";
    if (state.artifactValidation) return "started";
    return state.artifactPlan?.steps?.length ? "started" : "pending";
  }
  if (name === "Review") {
    if (state.evaluation?.decision === "ready_for_execution") return "complete";
    if (state.evaluation) return "started";
    return state.artifactValidation?.status === "passed" ? "started" : "pending";
  }
  return "pending";
}

function usesMultilineInput(field: { name: string; type: string }) {
  return (
    field.type === "object" ||
    field.type.endsWith("[]") ||
    ["transform_code", "sql_text", "artifact_sql", "probe_result", "runtime_evidence"].includes(
      field.name,
    )
  );
}

function skillInputHint(name: string) {
  const hints: Record<string, string> = {
    capability_evidence:
      "Paste indexed capability evidence. SQL can use Statement Execution evidence; ML can use MLflow/Python/Jobs evidence.",
    statement_capability_evidence:
      "Capability evidence for Databricks SQL Statement Execution.",
    jobs_capability_evidence:
      "Capability evidence for Databricks Jobs runs/submit and runs/get.",
    backend_capability_evidence:
      "Optional backend-specific capability evidence; leave empty to use strategy capability evidence.",
    training_artifact_uri:
      "URI of the Databricks-native training artifact generated or approved by the ML skill.",
    artifact_template_id:
      "Optional Databricks artifact template id, for example databricks.mlflow-flavor.tabular.",
    task_parameters:
      "Optional exact task parameters declared by the selected training artifact.",
    environment_dependencies:
      "Optional Jobs environment dependencies declared by the selected training artifact.",
    api_operations:
      "Only use concrete API operation evidence already grounded in capability refs.",
    statement_operation:
      "Optional Statement Execution operation override; usually leave blank.",
    job_submit_operation:
      "Optional Jobs runs/submit operation override; usually leave blank.",
    audit_readback_operation:
      "Statement Execution operation that reads the model training audit row after Jobs completes.",
    transform_code:
      "Paste the validated PySpark transform function: def transform(spark, inputs): ...",
    pyspark_driver_uri:
      "Serverless Jobs expects the deployed PySpark driver as a dbfs:/Volumes/... URI.",
    training_driver_uri:
      "Deprecated alias. Prefer training_artifact_uri for the selected Databricks-native training artifact.",
    probe_driver_uri:
      "URI for the deployed non-training backend probe driver artifact.",
    probe_result:
      "Observed row emitted by the backend probe driver. Use this after the probe job has run.",
    runtime_evidence:
      "Observed runtime facts from the probe. This must be real evidence, not an assertion.",
    feature_columns:
      "Use a JSON array or comma-separated list of feature columns.",
    dataset_profiles:
      "Use JSON array table profiles with table_ref, row_count, and columns.",
  };
  return hints[name] ?? "";
}

function skillInputPlaceholder(name: string, type: string) {
  const examples: Record<string, string> = {
    capability_evidence:
      '[{"entity_id":"openapi:2.0:StatementExecutionExecuteStatement"}]',
    statement_capability_evidence:
      '[{"entity_id":"openapi:2.0:StatementExecutionExecuteStatement"}]',
    jobs_capability_evidence:
      '[{"entity_id":"openapi:2.1:JobsRunsSubmit"},{"entity_id":"openapi:2.1:JobsRunsGet"}]',
    backend_capability_evidence:
      '[{"entity_id":"docs:databricks-mlflow"},{"entity_id":"openapi:2.1:JobsRunsSubmit"}]',
    training_artifact_uri:
      "dbfs:/Volumes/catalog/schema/artifacts/ml/customer_spend_train.py",
    artifact_template_id: "databricks.mlflow-flavor.tabular",
    task_parameters: '["--rows-uri","catalog.schema.training_rows","--model-full-name","catalog.schema.model"]',
    environment_dependencies: '["mlflow","scikit-learn","pandas"]',
    statement_operation:
      '{"operation_id":"openapi:2.0:StatementExecutionExecuteStatement","method":"POST","path":"/api/2.0/sql/statements","capability_refs":["openapi:2.0:StatementExecutionExecuteStatement"]}',
    job_submit_operation:
      '{"operation_id":"openapi:2.1:JobsRunsSubmit","method":"POST","path":"/api/2.1/jobs/runs/submit","capability_refs":["openapi:2.1:JobsRunsSubmit"]}',
    audit_readback_operation:
      `{"operation_id":"openapi:2.0:StatementExecutionReadAudit","method":"POST","path":"/api/2.0/sql/statements","body":{"statement":"SELECT to_json(named_struct(...)) FROM catalog.schema.audit WHERE audit_id = '...'","warehouse_id":"..."},"capability_refs":["openapi:2.0:StatementExecutionExecuteStatement"],"wait":{"kind":"sql_statement_succeeded","timeout_sec":120,"poll_sec":5}}`,
    dataset_profiles:
      '[{"table_ref":"catalog.schema.table","row_count":1000,"columns":[{"name":"id","data_type":"STRING"},{"name":"label","data_type":"DOUBLE"}]}]',
    feature_columns: '["feature_1","feature_2"]',
    expected_output_schema: '{"id":"STRING","metric":"DOUBLE"}',
    transform_code:
      'def transform(spark, inputs):\n    source = next(iter(inputs.values()))\n    return source.select("id")',
    sql_text: "CREATE OR REPLACE TABLE catalog.schema.output AS SELECT ...",
    artifact_sql: "CREATE OR REPLACE TABLE catalog.schema.output AS SELECT ...",
    pyspark_driver_uri:
      "dbfs:/Volumes/partner_demo_catalog/brickvision/brickvision_artifacts/pyspark/pyspark_transform_driver.py",
    training_driver_uri:
      "deprecated: use training_artifact_uri",
    probe_driver_uri:
      "dbfs:/Volumes/partner_demo_catalog/brickvision/brickvision_artifacts/ml/backend_probe_driver.py",
    runtime_surface: "serverless_jobs",
    runtime_evidence:
      '{"runtime_surface":"serverless_jobs","mlflow_uc_registry_available":true}',
    probe_result:
      '{"probe_id":"probe-...","runtime_surface":"serverless_jobs","substrate_json":{"python_imports":{"sklearn":true}}}',
    rows_uri: "catalog.schema.training_rows",
    model_full_name: "catalog.schema.model_name",
    audit_table: "catalog.schema.model_training_runs",
    val_metric_name: "rmse",
    val_metric_floor: "10.0",
    split_seed: "42",
  };
  if (examples[name]) return examples[name];
  if (type === "object") return '{"key":"value"}';
  if (type.endsWith("[]")) return '["value1","value2"]';
  return "";
}

function isResolvedSkillStatus(status: string) {
  return status === "available" || status === "covered";
}

function setSkillInputValue(
  setValue: Dispatch<SetStateAction<Record<string, Record<string, string>>>>,
  family: string,
  field: string,
  value: string,
) {
  setValue((current) => ({
    ...current,
    [family]: {
      ...(current[family] ?? {}),
      [field]: value,
    },
  }));
}
