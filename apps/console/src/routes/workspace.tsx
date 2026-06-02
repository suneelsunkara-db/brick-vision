import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import {
  ChevronDown,
  ChevronRight,
  Database,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/workspace")({
  component: WorkspacePage,
});

interface WorkspaceSummary {
  claim_count: number;
  subject_count: number;
  last_observed_at_ms: number | null;
  last_run_id: string | null;
  by_kind: Array<{ subject_kind: string; claim_count: number }>;
  indexer_state: string;
  message?: string;
}

interface WorkspaceClaim {
  claim_id: string;
  workspace_profile_id: string;
  workspace_id: string | null;
  subject: string;
  subject_kind: string;
  predicate: string;
  object_ref: string | null;
  value: unknown;
  metadata: unknown;
  source_skill_id: string;
  confidence: number | null;
  observed_at_ms: number | null;
  run_id: string | null;
}

interface WorkspaceClaimsPayload {
  claims: WorkspaceClaim[];
  total: number;
  query: string;
  subject_kind: string | null;
  limit: number;
  offset: number;
}

interface BuildSuggestion {
  suggestion_id: string;
  template_id: string;
  title: string;
  summary: string;
  confidence: number;
  target: {
    subject: string;
    schema_ref: string;
    catalog: string;
    schema: string;
    table_count: number;
    row_count: number;
  };
  evidence_summary: {
    row_count: number;
    table_count: number;
    column_count: number;
    profiled_column_count: number;
    candidate_key_columns: string[];
    null_risk_columns: string[];
    observed_at_ms: number;
  };
  included_tables: Array<{
    subject: string;
    table_ref: string;
    table_name: string;
    row_count: number;
    column_count: number;
    profiled_column_count: number;
    candidate_key_columns: string[];
    null_risk_columns: string[];
  }>;
  latest_build?: {
    status: string;
    artifact_kind: string;
    artifact_name: string;
    target_ref: string;
    updated_at_ms: number | null;
    execution_result?: {
      object_name?: string;
      message?: string;
    };
  };
  required_skills: string[];
  status: string;
}

interface BuildSuggestionsPayload {
  suggestions: BuildSuggestion[];
  active_snapshot_id?: string;
  indexer_state: string;
  message?: string;
  evidence_gate?: {
    passed: boolean;
    profiled_table_count: number;
    suggestion_count: number;
  };
}

interface BuildResult {
  status: string;
  suggestion_id: string;
  plan_id?: string;
  title?: string;
  active_snapshot_id?: string;
  target?: {
    subject: string;
    schema_ref?: string;
    catalog?: string;
    schema?: string;
    table_count?: number;
    row_count?: number;
  };
  artifact?: {
    kind: string;
    name?: string;
    filename: string;
    sql: string;
  };
  build_plan?: Array<{
    step_id: string;
    skill_id: string;
    description: string;
  }>;
  execution_result?: {
    executed?: boolean;
    object_type?: string;
    object_name?: string;
    message?: string;
    reason?: string;
    error_kind?: string;
  };
  next_action?: string;
  message?: string;
}

function WorkspacePage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [subjectKind, setSubjectKind] = useState("");
  const [selectedSubject, setSelectedSubject] = useState<string | null>(null);
  const [lastBuildResult, setLastBuildResult] = useState<BuildResult | null>(
    null,
  );
  const [activeSuggestionId, setActiveSuggestionId] = useState<string | null>(
    null,
  );

  const { data: summary } = useQuery({
    queryKey: ["workspace", "summary"],
    queryFn: () =>
      fetchJson<WorkspaceSummary>("/api/knowledge/workspace/summary"),
    staleTime: 60_000,
  });

  const { data: claims, isFetching } = useQuery({
    queryKey: ["workspace", "claims", submitted, subjectKind],
    queryFn: () =>
      fetchJson<WorkspaceClaimsPayload>("/api/knowledge/workspace/claims", {
        query: {
          q: submitted,
          subject_kind: subjectKind || undefined,
          limit: 200,
        },
      }),
    staleTime: 60_000,
  });

  const { data: buildSuggestions } = useQuery({
    queryKey: ["workspace", "build-suggestions"],
    queryFn: () =>
      fetchJson<BuildSuggestionsPayload>(
        "/api/knowledge/workspace/build-suggestions",
      ),
    staleTime: 60_000,
  });

  const buildMutation = useMutation({
    mutationFn: (suggestionId: string) =>
      fetchJson<BuildResult>(
        `/api/knowledge/workspace/build-suggestions/${encodeURIComponent(
          suggestionId,
        )}/plan-and-build`,
        { method: "POST" },
      ),
    onSuccess: (result) => {
      setLastBuildResult(result);
      void queryClient.invalidateQueries({
        queryKey: ["workspace", "build-suggestions"],
      });
    },
  });

  const handleSubmit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      setSubmitted(query.trim());
    },
    [query],
  );

  if (!summary) return <PanelSkeleton />;

  const visibleClaims = claims?.claims ?? [];
  const kinds = summary.by_kind.map((row) => row.subject_kind);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Workspace Context
        </h1>
        <p className="max-w-4xl text-sm text-muted-foreground">
          Partner workspace context: what exists in the active workspace profile.
          This page reads the serverless Workspace KG refresh output from Lakebase,
          not from live UI-triggered scans.
        </p>
      </header>

      {summary.claim_count === 0 ? (
        <EmptyState
          title="Workspace context has not been published."
          body={
            summary.message ??
            "Run the bv_workspace_kg_refresh serverless Job. The UI reads workspace_claims_current_synced from Lakebase."
          }
        />
      ) : (
        <>
          <WorkspaceSummaryCards summary={summary} />

          {buildSuggestions && (
            <BuildSuggestionsPanel
              payload={buildSuggestions}
              isBuilding={buildMutation.isPending}
              activeSuggestionId={activeSuggestionId}
              onBuild={(suggestionId) => {
                setActiveSuggestionId(suggestionId);
                buildMutation.mutate(suggestionId);
              }}
            />
          )}

          <UsecasePlanPanel
            result={lastBuildResult}
            suggestion={
              buildSuggestions?.suggestions.find(
                (item) => item.suggestion_id === lastBuildResult?.suggestion_id,
              ) ?? null
            }
          />

          <WorkspaceContextExplorer
            claims={visibleClaims}
            selectedSubject={selectedSubject}
            onSelect={setSelectedSubject}
          />

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Database className="h-4 w-4 text-primary" />
                Workspace Assets
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <FilterPill
                  active={subjectKind === ""}
                  label="All"
                  onClick={() => setSubjectKind("")}
                />
                {summary.by_kind.map((row) => (
                  <FilterPill
                    key={row.subject_kind}
                    active={subjectKind === row.subject_kind}
                    label={`${row.subject_kind} ${row.claim_count}`}
                    onClick={() => setSubjectKind(row.subject_kind)}
                  />
                ))}
              </div>

              <form onSubmit={handleSubmit} className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search workspace assets — e.g. customers, mfg_agent, function name"
                  className="w-full rounded-lg border border-border bg-background py-2.5 pl-10 pr-4 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                />
                {isFetching && (
                  <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
                )}
              </form>

              {claims && (
                <div className="text-xs text-muted-foreground">
                  Showing {claims.claims.length} of {claims.total} current claims
                  {subjectKind ? ` in ${subjectKind}` : ""}.
                </div>
              )}
            </CardContent>
          </Card>

          <div className="space-y-2">
            {visibleClaims.map((claim) => (
              <WorkspaceClaimCard key={claim.claim_id} claim={claim} />
            ))}
          </div>

          {claims && claims.claims.length === 0 && (
            <EmptyState
              title="No workspace assets match this filter."
              body={`Try a different search term or one of: ${kinds.join(", ")}.`}
            />
          )}
        </>
      )}
    </div>
  );
}

function WorkspaceSummaryCards({ summary }: { summary: WorkspaceSummary }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      <MetricCard label="Current claims" value={summary.claim_count} />
      <MetricCard label="Subjects" value={summary.subject_count} />
      <Card>
        <CardContent className="py-4">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            Last refresh run
          </div>
          <div className="mt-1 truncate font-mono text-xs">
            {summary.last_run_id ?? <Placeholder />}
          </div>
          {summary.last_observed_at_ms && (
            <div className="mt-1 text-[10px] text-muted-foreground">
              {new Date(summary.last_observed_at_ms).toLocaleString()}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
      </CardContent>
    </Card>
  );
}

function BuildSuggestionsPanel({
  payload,
  isBuilding,
  activeSuggestionId,
  onBuild,
}: {
  payload: BuildSuggestionsPayload;
  isBuilding: boolean;
  activeSuggestionId: string | null;
  onBuild: (suggestionId: string) => void;
}) {
  if (payload.suggestions.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-primary" />
            Evidence Starters
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {payload.message ??
            "No evidence starters passed the current evidence gate."}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-4 w-4 text-primary" />
          Evidence Starters
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {payload.evidence_gate && (
          <div className="text-xs text-muted-foreground">
            Evidence gate passed for {payload.evidence_gate.profiled_table_count}{" "}
            profiled tables on snapshot {payload.active_snapshot_id}.
          </div>
        )}

        <div className="grid gap-3 lg:grid-cols-2">
          {payload.suggestions.map((suggestion) => (
            <div
              key={suggestion.suggestion_id}
              className="rounded-lg border border-border p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="font-medium">{suggestion.title}</div>
                  {suggestion.latest_build && (
                    <div className="inline-flex rounded bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-400">
                      {suggestion.latest_build.status}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {suggestion.summary}
                  </p>
                </div>
                <span className="shrink-0 rounded bg-primary/10 px-2 py-1 text-[10px] font-medium text-primary">
                  {Math.round(suggestion.confidence * 100)}%
                </span>
              </div>

              <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                <EvidenceMetric
                  label="Tables"
                  value={suggestion.evidence_summary.table_count}
                />
                <EvidenceMetric
                  label="Rows"
                  value={
                    suggestion.evidence_summary.row_count > 0
                      ? suggestion.evidence_summary.row_count
                      : "Unknown"
                  }
                />
                <EvidenceMetric
                  label="Profiled cols"
                  value={suggestion.evidence_summary.profiled_column_count}
                />
              </div>

              <div className="mt-3 space-y-1 text-xs">
                <div className="font-medium text-muted-foreground">
                  Included tables
                </div>
                <div className="flex flex-wrap gap-1">
                  {suggestion.included_tables.slice(0, 8).map((table) => (
                    <span
                      key={table.table_ref}
                      className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground"
                    >
                      {table.table_name}
                    </span>
                  ))}
                  {suggestion.included_tables.length > 8 && (
                    <span className="rounded bg-muted/40 px-2 py-1 text-[10px] text-muted-foreground">
                      +{suggestion.included_tables.length - 8} more
                    </span>
                  )}
                </div>
              </div>

              {suggestion.evidence_summary.candidate_key_columns.length > 0 && (
                <div className="mt-2 text-xs text-muted-foreground">
                  Candidate grains:{" "}
                  {suggestion.evidence_summary.candidate_key_columns
                    .slice(0, 4)
                    .join(", ")}
                </div>
              )}

              {suggestion.latest_build && (
                <div className="mt-2 space-y-1 rounded bg-muted/20 p-2 text-xs text-muted-foreground">
                  <div>
                    Latest artifact:{" "}
                    <span className="font-mono text-[10px]">
                      {suggestion.latest_build.artifact_name}
                    </span>
                  </div>
                  {suggestion.latest_build.execution_result?.object_name && (
                    <div className="font-mono text-[10px]">
                      {suggestion.latest_build.execution_result.object_name}
                    </div>
                  )}
                </div>
              )}

              <div className="mt-3 flex items-center justify-between gap-3">
                <div className="truncate font-mono text-[10px] text-muted-foreground">
                  {suggestion.target.schema_ref}
                </div>
                <Button
                  size="sm"
                  disabled={isBuilding}
                  onClick={() => onBuild(suggestion.suggestion_id)}
                >
                  {isBuilding && activeSuggestionId === suggestion.suggestion_id
                    ? "Creating starter artifact..."
                    : suggestion.status === "built"
                      ? "Update Starter Artifact"
                      : "Create Starter Artifact"}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function UsecasePlanPanel({
  result,
  suggestion,
}: {
  result: BuildResult | null;
  suggestion: BuildSuggestion | null;
}) {
  if (!result) {
    return null;
  }

  const stages = starterLifecycleStages(result);
  const targetRef =
    result.target?.schema_ref ?? suggestion?.target.schema_ref ?? "unknown target";

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-4 w-4 text-primary" />
          Technical Starter Artifact
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-2">
            <div className="text-lg font-semibold">
              {result.title ?? suggestion?.title ?? "Starter artifact"}
            </div>
            <p className="text-sm text-muted-foreground">
              This is a technical artifact produced from selected schema
              evidence. It is not yet a business usecase; the next UI surface
              must add outcome, persona, value, skills, validation, deployment,
              and outcome proof.
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              <StatusPill status={result.status} />
              <span className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                {targetRef}
              </span>
              {result.active_snapshot_id && (
                <span className="rounded bg-muted/40 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                  {result.active_snapshot_id}
                </span>
              )}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs">
            <div className="font-medium">Generated technical artifact</div>
            <div className="mt-2 space-y-1 text-muted-foreground">
              <div>
                Type:{" "}
                <span className="font-mono">
                  {result.artifact?.kind ?? result.execution_result?.object_type ?? "-"}
                </span>
              </div>
              <div>
                Name:{" "}
                <span className="font-mono">
                  {result.artifact?.name ??
                    result.artifact?.filename ??
                    result.execution_result?.object_name ??
                    "-"}
                </span>
              </div>
              {result.execution_result?.message && (
                <div>{result.execution_result.message}</div>
              )}
            </div>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
            <div className="text-sm font-medium text-amber-300">
              Business Usecase Candidate
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Not inferred yet. This starter needs an outcome template, business
              persona, value hypothesis, and acceptance criteria before it can
              become a business usecase.
            </p>
            <Button asChild size="sm" variant="outline" className="mt-3">
              <Link to="/usecases">Open Usecases</Link>
            </Button>
          </div>

          <div className="rounded-lg border border-border bg-muted/10 p-3">
            <div className="text-sm font-medium">Required Skills</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {starterSkillReadiness().map((skill) => (
                <span
                  key={skill.name}
                  className={cn(
                    "rounded px-2 py-1 text-[10px] font-medium",
                    skill.state === "available"
                      ? "bg-emerald-500/10 text-emerald-400"
                      : "bg-muted/50 text-muted-foreground",
                  )}
                >
                  {skill.name}: {skill.state}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
          {stages.map((stage) => (
            <div
              key={stage.name}
              className={cn(
                "rounded-lg border p-3",
                stage.state === "complete"
                  ? "border-emerald-500/30 bg-emerald-500/10"
                  : stage.state === "active"
                    ? "border-primary/40 bg-primary/10"
                    : "border-border bg-muted/10",
              )}
            >
              <div className="text-xs font-medium">{stage.name}</div>
              <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                {stage.state}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {stage.description}
              </p>
            </div>
          ))}
        </div>

        {result.build_plan && result.build_plan.length > 0 && (
          <div className="space-y-2">
            <div className="text-sm font-medium">Plan Steps</div>
            <div className="grid gap-2 lg:grid-cols-3">
              {result.build_plan.map((step) => (
                <div
                  key={step.step_id}
                  className="rounded-lg border border-border bg-background p-3 text-xs"
                >
                  <div className="font-mono text-[10px] text-primary">
                    {step.skill_id}
                  </div>
                  <div className="mt-1 font-medium">{step.step_id}</div>
                  <p className="mt-2 text-muted-foreground">{step.description}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {result.artifact?.sql && (
          <details className="rounded-lg border border-border bg-muted/20 p-3">
            <summary className="cursor-pointer text-sm font-medium">
              View Generated SQL
            </summary>
            <pre className="mt-3 overflow-x-auto rounded bg-background p-3 text-xs">
              {result.artifact.sql}
            </pre>
          </details>
        )}

        {result.next_action && (
          <div className="rounded-lg bg-primary/10 p-3 text-sm text-primary">
            {result.next_action}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className="rounded bg-primary/10 px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-primary">
      {status}
    </span>
  );
}

function starterLifecycleStages(result: BuildResult) {
  const built = result.status === "built";
  const blocked = result.status === "blocked_evidence_drift";
  const failed = result.status === "build_failed";

  return [
    {
      name: "Evidence",
      state: "complete",
      description: "Schema evidence and required skill anchors passed the gate.",
    },
    {
      name: "Plan",
      state: blocked ? "blocked" : "complete",
      description: blocked
        ? "Evidence changed before execution."
        : "A deterministic plan was persisted for the selected schema.",
    },
    {
      name: "Build",
      state: built ? "complete" : failed ? "blocked" : "active",
      description: built
        ? "Technical starter artifact was created."
        : failed
          ? "Artifact execution failed."
          : "Starter artifact is ready for execution.",
    },
    {
      name: "Validate",
      state: "next",
      description: "Check row-count, null-count, distinct-count, and grain signals.",
    },
    {
      name: "Evaluate",
      state: "next",
      description: "Compare expected value, quality impact, and readiness criteria.",
    },
    {
      name: "Deploy",
      state: "next",
      description: "Promote only after this is attached to a business usecase.",
    },
  ] as const;
}

function starterSkillReadiness() {
  return [
    { name: "SQL", state: "available" },
    { name: "PySpark", state: "missing" },
    { name: "ML", state: "missing" },
    { name: "AI", state: "missing" },
    { name: "Deploy", state: "not selected" },
  ] as const;
}

function EvidenceMetric({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded bg-muted/30 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wider">{label}</div>
      <div className="font-medium text-foreground">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}

function FilterPill({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-xs",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border text-muted-foreground",
      )}
    >
      {label}
    </button>
  );
}

function WorkspaceClaimCard({ claim }: { claim: WorkspaceClaim }) {
  return (
    <Card>
      <CardContent className="space-y-2 py-3 text-xs">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                {claim.subject_kind}
              </span>
              <span className="break-all font-mono text-sm">{claim.subject}</span>
            </div>
            <div className="break-all font-mono text-[10px] text-muted-foreground">
              {claim.predicate}
              {claim.object_ref ? ` -> ${claim.object_ref}` : ""}
            </div>
          </div>
          <div className="shrink-0 text-right text-[10px] text-muted-foreground">
            <div>{claim.workspace_profile_id}</div>
            {claim.confidence !== null && (
              <div>confidence {claim.confidence.toFixed(2)}</div>
            )}
          </div>
        </div>
        {claim.metadata !== null && claim.metadata !== undefined && (
          <details className="rounded border border-border/60 bg-muted/20 px-2 py-1">
            <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-muted-foreground">
              metadata
            </summary>
            <pre className="mt-2 overflow-x-auto text-[10px] leading-relaxed">
              {JSON.stringify(claim.metadata, null, 2)}
            </pre>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

interface ContextNode {
  id: string;
  label: string;
  kind: string;
  claim?: WorkspaceClaim;
  children: ContextNode[];
}

function WorkspaceContextExplorer({
  claims,
  selectedSubject,
  onSelect,
}: {
  claims: WorkspaceClaim[];
  selectedSubject: string | null;
  onSelect: (subject: string) => void;
}) {
  const roots = useMemo(() => buildWorkspaceTree(claims), [claims]);
  const selectedClaim = claims.find((claim) => claim.subject === selectedSubject);

  if (roots.length === 0) {
    return (
      <Card>
        <CardContent className="py-4 text-sm text-muted-foreground">
          No hierarchy available for the current filter.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Workspace Context Explorer</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="max-h-[460px] overflow-auto rounded-lg border border-border/60 bg-muted/20 p-2">
          {roots.map((root) => (
            <TreeNodeView
              key={root.id}
              node={root}
              depth={0}
              selectedSubject={selectedSubject}
              onSelect={onSelect}
            />
          ))}
        </div>
        <ContextDetails claim={selectedClaim} />
      </CardContent>
    </Card>
  );
}

function TreeNodeView({
  node,
  depth,
  selectedSubject,
  onSelect,
}: {
  node: ContextNode;
  depth: number;
  selectedSubject: string | null;
  onSelect: (subject: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  const hasChildren = node.children.length > 0;
  const selected = selectedSubject === node.id;

  return (
    <div>
      <div
        className={cn(
          "flex items-center gap-1 rounded px-2 py-1 text-xs",
          selected ? "bg-primary/10 text-primary" : "hover:bg-background/60",
        )}
        style={{ paddingLeft: `${depth * 18 + 8}px` }}
      >
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((current) => !current)}
          disabled={!hasChildren}
          aria-label={open ? "Collapse" : "Expand"}
        >
          {hasChildren ? (
            open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )
          ) : (
            <span className="block h-3.5 w-3.5" />
          )}
        </button>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className="min-w-0 flex-1 truncate text-left font-mono"
          title={node.id}
        >
          <span className="mr-2 rounded bg-background px-1 py-0.5 text-[10px] text-muted-foreground">
            {node.kind}
          </span>
          {shortName(node.label)}
          {hasChildren && (
            <span className="ml-2 text-[10px] text-muted-foreground">
              {node.children.length}
            </span>
          )}
        </button>
      </div>
      {open && hasChildren && (
        <div>
          {node.children.map((child) => (
            <TreeNodeView
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedSubject={selectedSubject}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ContextDetails({ claim }: { claim?: WorkspaceClaim }) {
  if (!claim) {
    return (
      <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
        Select a catalog, schema, table, view, function, or volume to inspect
        its current context.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-border/60 p-4 text-xs">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Selected context
        </div>
        <div className="mt-1 break-all font-mono text-sm">{claim.subject}</div>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">kind</dt>
        <dd className="font-mono">{claim.subject_kind}</dd>
        <dt className="text-muted-foreground">relationship</dt>
        <dd className="break-all font-mono">
          {claim.predicate}
          {claim.object_ref ? ` -> ${claim.object_ref}` : ""}
        </dd>
        <dt className="text-muted-foreground">profile</dt>
        <dd className="font-mono">{claim.workspace_profile_id}</dd>
        <dt className="text-muted-foreground">source</dt>
        <dd className="font-mono">{claim.source_skill_id}</dd>
      </dl>
      {claim.metadata !== null && claim.metadata !== undefined && (
        <details className="rounded border border-border/60 bg-muted/20 px-2 py-1">
          <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-muted-foreground">
            metadata
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto text-[10px] leading-relaxed">
            {JSON.stringify(claim.metadata, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

function buildWorkspaceTree(claims: WorkspaceClaim[]): ContextNode[] {
  const nodes = new Map<string, ContextNode>();
  const childIds = new Set<string>();

  const getNode = (id: string, kind: string, claim?: WorkspaceClaim) => {
    const existing = nodes.get(id);
    if (existing) {
      if (claim) existing.claim = claim;
      return existing;
    }
    const node: ContextNode = {
      id,
      label: id,
      kind,
      claim,
      children: [],
    };
    nodes.set(id, node);
    return node;
  };

  for (const claim of claims) {
    const node = getNode(claim.subject, claim.subject_kind, claim);
    if (claim.object_ref) {
      const parent = getNode(claim.object_ref, kindFromSubject(claim.object_ref));
      if (!parent.children.some((child) => child.id === node.id)) {
        parent.children.push(node);
      }
      childIds.add(node.id);
    }
  }

  for (const node of nodes.values()) {
    node.children.sort(compareContextNodes);
  }

  return Array.from(nodes.values())
    .filter((node) => !childIds.has(node.id))
    .sort(compareContextNodes);
}

function kindFromSubject(subject: string): string {
  if (subject.startsWith("catalog:")) return "CATALOG";
  if (subject.startsWith("schema:")) return "SCHEMA";
  return "OBJECT";
}

function compareContextNodes(a: ContextNode, b: ContextNode): number {
  const weight = (kind: string) =>
    kind === "CATALOG" ? 0 : kind === "SCHEMA" ? 1 : 2;
  return weight(a.kind) - weight(b.kind) || a.label.localeCompare(b.label);
}

function shortName(label: string): string {
  return label.replace(/^(catalog:|schema:|table:|view:|function:|volume:)/, "");
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <Card className="border-dashed">
      <CardContent className="space-y-2 py-8">
        <div className="font-medium">{title}</div>
        <p className="max-w-3xl text-sm text-muted-foreground">{body}</p>
      </CardContent>
    </Card>
  );
}

function Placeholder() {
  return <span className="text-muted-foreground">-</span>;
}
