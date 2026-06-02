import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Brain, CheckCircle2, Hammer, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/skill-builder")({
  component: SkillBuilderPage,
});

interface SkillField {
  name: string;
  type?: string;
  required?: boolean;
  description?: string;
}

interface SkillContract {
  skill_id: string;
  title: string;
  version: string;
  exemplar_of: string;
  category: string;
  description: string;
  when_to_use: string[];
  triggers: string[];
  model_role: string;
  tools: string[];
  required_skills: string[];
  inputs: SkillField[];
  outputs: SkillField[];
  scorers: string[];
  runtime: string;
  on_failure: string;
  owner: string;
  skill_dir: string;
  readiness: {
    status: string;
    label: string;
    message: string;
    blocking_count: number;
  };
}

interface SkillBuilderPayload {
  status: string;
  next_action: string;
  summary: {
    skill_count: number;
    ready_count: number;
    needs_work_count: number;
    by_category: Record<string, number>;
  };
  execution_families: Array<{
    family: string;
    status: string;
    label: string;
    skill_ids: string[];
    message: string;
  }>;
  skill_gaps: Array<{
    gap_id: string;
    title: string;
    missing_skill_id: string;
    status: string;
    priority: string;
    evidence: string;
    why_build: string;
    recommended_first_step: string;
  }>;
  skills: SkillContract[];
}

function SkillBuilderPage() {
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<"gaps" | "available" | "build">("gaps");
  const { data, isLoading } = useQuery({
    queryKey: ["skill-builder", "skills"],
    queryFn: () =>
      fetchJson<SkillBuilderPayload>("/api/knowledge/skill-builder/skills"),
    staleTime: 60_000,
  });

  const skills = data?.skills ?? [];
  const gaps = data?.skill_gaps ?? [];
  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.skill_id === selectedSkillId) ?? skills[0],
    [selectedSkillId, skills],
  );
  const grouped = useMemo(() => groupByCategory(skills), [skills]);

  if (isLoading) return <PanelSkeleton />;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Skills</h1>
        <p className="max-w-4xl text-sm text-muted-foreground">
          Skills are BrickVision's reusable capabilities. Use this page to see
          what is ready, what gaps are blocking workflows, and how an agent should
          draft the next skill.
        </p>
      </header>

      <div className="grid gap-3 md:grid-cols-3">
        <SummaryCard label="Skills" value={data?.summary.skill_count ?? 0} />
        <SummaryCard label="Ready to use" value={data?.summary.ready_count ?? 0} />
        <SummaryCard label="Needs work" value={data?.summary.needs_work_count ?? 0} />
      </div>

      {data?.next_action && (
        <div className="rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm text-primary">
          {data.next_action}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <ViewButton active={activeView === "gaps"} onClick={() => setActiveView("gaps")}>
          Gaps
        </ViewButton>
        <ViewButton active={activeView === "available"} onClick={() => setActiveView("available")}>
          Available
        </ViewButton>
        <ViewButton active={activeView === "build"} onClick={() => setActiveView("build")}>
          Build
        </ViewButton>
      </div>

      {data?.execution_families && data.execution_families.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4 text-primary" />
              Capability Readiness Gates
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-4">
            {data.execution_families.map((family) => (
              <div
                key={family.family}
                className="rounded-lg border border-border bg-muted/10 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium">{family.family}</div>
                  <ReadinessBadge status={family.status}>{family.label}</ReadinessBadge>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">{family.message}</p>
                {family.skill_ids.length > 0 && (
                  <div className="mt-3 space-y-1">
                    {family.skill_ids.map((skillId) => (
                      <div
                        key={skillId}
                        className="font-mono text-[10px] text-muted-foreground"
                      >
                        {skillId}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {activeView === "gaps" && <SkillGaps gaps={gaps} />}

      {activeView === "available" && (
        <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
          <SkillList
            grouped={grouped}
            selectedSkillId={selectedSkill?.skill_id}
            onSelect={setSelectedSkillId}
          />
          {selectedSkill && <SkillDetail skill={selectedSkill} />}
        </div>
      )}

      {activeView === "build" && <BuildSkillFlow gaps={gaps} />}
    </div>
  );
}

function ViewButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1.5 text-sm transition-colors",
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border bg-muted/10 text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function SkillGaps({ gaps }: { gaps: SkillBuilderPayload["skill_gaps"] }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_0.75fr]">
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-primary" />
            Recommended Skill Gaps
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {gaps.map((gap) => (
            <div key={gap.gap_id} className="rounded-lg border border-border bg-muted/10 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{gap.title}</div>
                  <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                    {gap.missing_skill_id}
                  </div>
                </div>
                <ReadinessBadge status={gap.priority === "critical" || gap.priority === "high" ? "needs_work" : "ready"}>
                  {gap.priority}
                </ReadinessBadge>
              </div>
              <p className="mt-3 text-sm text-muted-foreground">{gap.why_build}</p>
              <div className="mt-3 rounded bg-background/70 p-2 text-xs text-muted-foreground">
                Evidence: {gap.evidence}
              </div>
              <div className="mt-2 rounded bg-primary/10 p-2 text-xs text-primary">
                First step: {gap.recommended_first_step}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Why This View Exists</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            This is the actual Skill Builder backlog: missing skills backed by
            runtime evidence, capability graph coverage, or blocked workflows.
          </p>
          <p>
            The first migration skill to build is the support matrix. It tells
            agents what Lakebridge can honestly support before they draft
            assessment, transpile, or reconcile skills.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function BuildSkillFlow({ gaps }: { gaps: SkillBuilderPayload["skill_gaps"] }) {
  const firstGap = gaps[0];
  return (
    <Card className="border-primary/30 bg-primary/5">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Hammer className="h-4 w-4 text-primary" />
          Agent-Assisted Skill Build
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-muted-foreground">
        <p>
          This is the intended Skill Builder flow. BrickVision should invoke an
          agent with a selected gap, gather evidence through existing skills, then
          draft and validate the new skill contract.
        </p>
        {firstGap && (
          <div className="rounded-lg border border-border bg-background/80 p-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Recommended starting point
            </div>
            <div className="mt-2 font-medium text-foreground">{firstGap.title}</div>
            <div className="mt-1 font-mono text-[10px]">{firstGap.missing_skill_id}</div>
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-4">
          {[
            "Gather evidence",
            "Draft SKILL.yaml",
            "Draft skill.py",
            "Run validation",
          ].map((step, index) => (
            <div key={step} className="rounded border border-border bg-background/70 p-3">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Step {index + 1}
              </div>
              <div className="mt-2 font-medium text-foreground">{step}</div>
            </div>
          ))}
        </div>
        <p className="text-xs">
          The build button is intentionally not active yet. The next backend slice
          should create persisted SkillGap records and an agent run that writes a
          draft skill in review mode.
        </p>
      </CardContent>
    </Card>
  );
}

function SkillList({
  grouped,
  selectedSkillId,
  onSelect,
}: {
  grouped: Record<string, SkillContract[]>;
  selectedSkillId?: string;
  onSelect: (skillId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-4 w-4 text-primary" />
          Available Skills
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {Object.entries(grouped).map(([category, categorySkills]) => (
          <div key={category} className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {category} ({categorySkills.length})
            </div>
            <div className="space-y-2">
              {categorySkills.map((skill) => (
                <button
                  key={skill.skill_id}
                  type="button"
                  onClick={() => onSelect(skill.skill_id)}
                  className={cn(
                    "w-full rounded-lg border p-3 text-left transition-colors",
                    selectedSkillId === skill.skill_id
                      ? "border-primary/50 bg-primary/10"
                      : "border-border bg-muted/10 hover:bg-muted/20",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium">{skill.title}</div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                        {skill.skill_id}
                      </div>
                    </div>
                    <ReadinessBadge status={skill.readiness.status}>
                      {skill.readiness.label}
                    </ReadinessBadge>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SkillDetail({ skill }: { skill: SkillContract }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{skill.title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span className="rounded bg-muted/40 px-2 py-1 font-mono">{skill.skill_id}</span>
          <span className="rounded bg-muted/40 px-2 py-1">v{skill.version}</span>
          <span className="rounded bg-muted/40 px-2 py-1">{skill.category}</span>
          {skill.model_role && (
            <span className="rounded bg-muted/40 px-2 py-1">
              Model role: {skill.model_role}
            </span>
          )}
        </div>

        <ReadinessPanel skill={skill} />

        <Section title="What This Skill Does" Icon={Brain}>
          <p className="whitespace-pre-line text-sm text-muted-foreground">
            {skill.description || "No description declared."}
          </p>
        </Section>

        <Section title="When BrickVision Should Use It" Icon={CheckCircle2}>
          <BulletList items={skill.when_to_use} empty="No usage guidance declared." />
        </Section>

        <div className="grid gap-4 md:grid-cols-2">
          <FieldSection title="Required Details" fields={skill.inputs} />
          <FieldSection title="Outputs Created" fields={skill.outputs} />
        </div>

        <Section title="Tools BrickVision Will Use" Icon={Wrench}>
          <TokenList items={skill.tools} empty="No tools declared." monospace />
        </Section>

        <Section title="Quality Checks" Icon={ShieldCheck}>
          <TokenList items={skill.scorers} empty="No checks declared." />
        </Section>

        <Section title="Contract Anchor" Icon={Hammer}>
          <div className="space-y-2 text-xs text-muted-foreground">
            <div>
              Capability Graph anchor:{" "}
              <span className="font-mono">{skill.exemplar_of || "not declared"}</span>
            </div>
            <div>
              Source folder: <span className="font-mono">{skill.skill_dir}</span>
            </div>
            <div>
              Owner: <span className="font-mono">{skill.owner || "not declared"}</span>
            </div>
          </div>
        </Section>
      </CardContent>
    </Card>
  );
}

function ReadinessPanel({ skill }: { skill: SkillContract }) {
  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm",
        skill.readiness.status === "ready"
          ? "border-emerald-500/30 bg-emerald-500/10"
          : "border-amber-500/30 bg-amber-500/10",
      )}
    >
      <div className="font-medium">
        {skill.readiness.status === "ready" ? "Ready to use" : "Needs work"}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{skill.readiness.message}</p>
    </div>
  );
}

function FieldSection({ title, fields }: { title: string; fields: SkillField[] }) {
  return (
    <Section title={title} Icon={Hammer}>
      {fields.length === 0 ? (
        <div className="text-sm text-muted-foreground">No fields declared.</div>
      ) : (
        <div className="space-y-2">
          {fields.map((field) => (
            <div key={field.name} className="rounded border border-border bg-muted/10 p-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{field.name}</span>
                {field.type && (
                  <span className="rounded bg-muted/40 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
                    {field.type}
                  </span>
                )}
                {field.required && (
                  <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300">
                    Required
                  </span>
                )}
              </div>
              {field.description && (
                <div className="mt-1 text-xs text-muted-foreground">
                  {field.description}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function Section({
  title,
  Icon,
  children,
}: {
  title: string;
  Icon: typeof Brain;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-3">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Icon className="h-4 w-4 text-primary" />
        {title}
      </div>
      {children}
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className="mt-2 text-2xl font-semibold">{value}</div>
      </CardContent>
    </Card>
  );
}

function ReadinessBadge({
  status,
  children,
}: {
  status: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "shrink-0 rounded px-2 py-1 text-[10px] font-medium uppercase tracking-wide",
        status === "ready"
          ? "bg-emerald-500/10 text-emerald-400"
          : "bg-amber-500/10 text-amber-300",
      )}
    >
      {children}
    </span>
  );
}

function BulletList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <div className="text-sm text-muted-foreground">{empty}</div>;
  }
  return (
    <div className="space-y-1 text-sm text-muted-foreground">
      {items.map((item) => (
        <div key={item}>- {item}</div>
      ))}
    </div>
  );
}

function TokenList({
  items,
  empty,
  monospace = false,
}: {
  items: string[];
  empty: string;
  monospace?: boolean;
}) {
  if (items.length === 0) {
    return <div className="text-sm text-muted-foreground">{empty}</div>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className={cn(
            "rounded bg-muted/50 px-2 py-1 text-xs text-muted-foreground",
            monospace && "font-mono text-[10px]",
          )}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function groupByCategory(skills: SkillContract[]) {
  return skills.reduce<Record<string, SkillContract[]>>((groups, skill) => {
    const bucket = groups[skill.category] ?? [];
    bucket.push(skill);
    groups[skill.category] = bucket;
    return groups;
  }, {});
}
