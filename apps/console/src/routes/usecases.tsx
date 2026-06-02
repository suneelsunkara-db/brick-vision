import { useState } from "react";
import {
  Outlet,
  createFileRoute,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Brain,
  Sparkles,
  Target,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/usecases")({
  component: UsecasesPage,
});

interface UsecaseCandidate {
  candidate_id: string;
  source_suggestion_id?: string;
  title: string;
  status: string;
  readiness: string;
  confidence: number;
  outcome: string;
  persona: string;
  value_hypothesis: string;
  evidence_summary: string;
  proposal_kind?: string;
  suggested_strategy?: string;
  why_proposed?: string;
  evidence_refs: Array<{ kind: string; ref: string }>;
  required_skill_families: Array<{ family: string; status: string }>;
  missing_inputs: string[];
  starter_artifacts: Array<{
    kind: string;
    template_id: string;
    title?: string;
    status?: string;
    target_ref: string;
  }>;
  next_action: string;
}

interface UsecaseCandidatesPayload {
  candidates: UsecaseCandidate[];
  active_snapshot_id?: string;
  indexer_state: string;
  message?: string;
  source?: {
    kind: string;
    evidence_gate?: {
      profiled_table_count?: number;
      ready_table_count?: number;
      suggestion_count?: number;
    };
  };
}

interface UsecaseRecord {
  usecase_id?: string;
  status: string;
  message?: string;
}

const SKILL_FAMILIES = [
  { name: "SQL", state: "available", detail: "Current starter build path." },
  { name: "PySpark", state: "needed", detail: "Needed for richer data products and profiling workflows." },
  { name: "ML", state: "needed", detail: "Needed for training, registering, and serving models." },
  { name: "AI", state: "needed", detail: "Needed for RAG and agentic usecases." },
  { name: "Migration", state: "candidate", detail: "Lakebridge starter proven; full assessment pending." },
  { name: "Deploy", state: "pending", detail: "Requires approval gates and target selection." },
] as const;

function UsecasesPage() {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["usecases", "candidates"],
    queryFn: () =>
      fetchJson<UsecaseCandidatesPayload>("/api/knowledge/usecases/candidates"),
    staleTime: 60_000,
  });
  const createMutation = useMutation({
    mutationFn: (candidateId: string) =>
      fetchJson<UsecaseRecord>("/api/knowledge/usecases", {
        method: "POST",
        body: JSON.stringify({ candidate_id: candidateId }),
      }),
    onSuccess: (record) => {
      if (record.usecase_id) {
        void navigate({
          to: "/usecases/$usecaseId",
          params: { usecaseId: record.usecase_id },
        });
      }
    },
    onSettled: () => setActiveCandidateId(null),
  });

  if (pathname !== "/usecases") {
    return <Outlet />;
  }

  if (isLoading) return <PanelSkeleton />;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Usecase Opportunities</h1>
        <p className="max-w-4xl text-sm text-muted-foreground">
          Start from KG-backed opportunities. A usecase explains why work matters;
          skills and workflows decide how BrickVision assesses, converts, builds,
          validates, or deploys the artifacts behind it.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_0.8fr]">
        <CandidateColumn
          payload={data}
          activeCandidateId={activeCandidateId}
          isCreating={createMutation.isPending}
          onCreate={(candidateId) => {
            setActiveCandidateId(candidateId);
            createMutation.mutate(candidateId);
          }}
        />
        <UsecaseDirectionCard payload={data} />
      </div>

      <CapabilityDependencies />
    </div>
  );
}

function CandidateColumn({
  payload,
  activeCandidateId,
  isCreating,
  onCreate,
}: {
  payload?: UsecaseCandidatesPayload;
  activeCandidateId: string | null;
  isCreating: boolean;
  onCreate: (candidateId: string) => void;
}) {
  const candidates = payload?.candidates ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Target className="h-4 w-4 text-primary" />
          KG-Derived Opportunity Proposals
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {candidates.length === 0 && (
          <div className="rounded-lg border border-border bg-muted/20 p-3 text-sm text-muted-foreground">
            {payload?.message ??
              "No business candidates can be compiled until evidence starters pass the current evidence gate."}
          </div>
        )}

        {candidates.map((candidate) => (
          <div
            key={candidate.candidate_id}
            className="rounded-lg border border-border p-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <button
                  type="button"
                  className="text-left font-medium text-foreground hover:text-primary"
                  disabled={isCreating}
                  onClick={() => onCreate(candidate.candidate_id)}
                >
                  {candidate.title}
                </button>
                <div className="mt-1 text-xs text-muted-foreground">
                  {candidate.persona}
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <StatusBadge status={candidate.status} />
                <span className="text-[10px] text-muted-foreground">
                  {Math.round(candidate.confidence * 100)}%
                </span>
              </div>
            </div>

            <p className="mt-3 text-sm text-muted-foreground">
              {candidate.value_hypothesis}
            </p>
            {candidate.why_proposed && (
              <p className="mt-2 rounded border border-border bg-muted/20 p-2 text-xs text-muted-foreground">
                Why proposed: {candidate.why_proposed}
              </p>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
              {candidate.evidence_summary}
            </p>

            <div className="mt-3 flex flex-wrap gap-1">
              {candidate.proposal_kind && (
                <span className="rounded bg-muted/50 px-2 py-1 text-[10px] text-muted-foreground">
                  Type: {candidate.proposal_kind}
                </span>
              )}
              {candidate.suggested_strategy && (
                <span className="rounded bg-muted/50 px-2 py-1 text-[10px] text-muted-foreground">
                  Strategy: {candidate.suggested_strategy}
                </span>
              )}
            </div>

            <div className="mt-3 flex flex-wrap gap-1">
              {candidate.required_skill_families.map((skill) => (
                <span
                  key={`${candidate.candidate_id}:${skill.family}`}
                  className="rounded bg-primary/10 px-2 py-1 text-[10px] text-primary"
                >
                  {skill.family}: {skill.status}
                </span>
              ))}
            </div>

            <div className="mt-3 flex flex-wrap gap-1">
              {candidate.missing_inputs.map((gap) => (
                <span
                  key={gap}
                  className="rounded bg-muted/50 px-2 py-1 text-[10px] text-muted-foreground"
                >
                  Missing: {gap}
                </span>
              ))}
            </div>

            <div className="mt-3 rounded bg-muted/20 p-2 text-xs text-muted-foreground">
              {candidate.next_action}
            </div>

            <div className="mt-3 flex justify-end">
              <Button
                size="sm"
                disabled={isCreating}
                onClick={() => onCreate(candidate.candidate_id)}
              >
                {isCreating && activeCandidateId === candidate.candidate_id
                  ? "Opening proposal..."
                  : "Review Opportunity"}
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function UsecaseDirectionCard({ payload }: { payload?: UsecaseCandidatesPayload }) {
  const gate = payload?.source?.evidence_gate;

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles className="h-4 w-4 text-primary" />
          Product Boundary
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-muted-foreground">
        <p>
          Workspace Context and the Capability Graph answer what exists. Usecase
          opportunities decide which business or modernization outcome is worth
          pursuing. Skills provide reusable capabilities, and workflows orchestrate
          those skills into runs with evidence.
        </p>
        {gate && (
          <div className="grid gap-2 text-xs sm:grid-cols-3">
            <Metric label="Profiled tables" value={gate.profiled_table_count} />
            <Metric label="Ready tables" value={gate.ready_table_count} />
            <Metric label="Starters" value={gate.suggestion_count} />
          </div>
        )}
        <p className="text-xs">
          Accepted opportunities open a detail page where evidence, strategy,
          required capabilities, and linked workflow runs are reviewed.
        </p>
      </CardContent>
    </Card>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value?: number;
}) {
  return (
    <div className="rounded border border-border bg-background p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 font-semibold text-foreground">{value ?? "-"}</div>
    </div>
  );
}

function CapabilityDependencies() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-4 w-4 text-primary" />
          Capability Dependencies
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {SKILL_FAMILIES.map((skill) => (
            <div key={skill.name} className="rounded-lg border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium">{skill.name}</div>
                <StatusBadge status={skill.state} />
              </div>
              <p className="mt-2 text-xs text-muted-foreground">{skill.detail}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded px-2 py-1 text-[10px] font-medium uppercase tracking-wide",
        status === "available" || status === "candidate"
          ? "bg-emerald-500/10 text-emerald-400"
          : status === "not_ready" || status === "needed"
            ? "bg-amber-500/10 text-amber-300"
            : "bg-muted/50 text-muted-foreground",
      )}
    >
      {displayStatus(status)}
    </span>
  );
}

function displayStatus(status: string) {
  const labels: Record<string, string> = {
    proposed: "Proposed",
    candidate: "Proposed",
    available: "Ready",
    needed: "Needed",
    pending: "Not started",
    recommended: "Recommended",
    not_required_yet: "Not needed yet",
    missing: "Needed",
    needs_target: "Needs target",
    optional: "Optional",
    blocked_by_empty_data: "Blocked by empty data",
    not_ready: "Not ready",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}
