import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import {
  AlertTriangle,
  Boxes,
  Database,
  Globe2,
  Layers,
  ListTree,
  Loader2,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorBoundary } from "@/components/error-boundary";
import { PanelSkeleton } from "@/components/ui/skeleton";
import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

/*
 * v0.7.7 — Knowledge UI (`/knowledge`).
 *
 * Per docs/12-visual-builder.md §10.8 (NEW v0.7.7): partner-side
 * page that surfaces the Databricks Capability Graph indexed nightly
 * by the `bv_capability_indexer` Job. 5 tabs:
 *
 *   1. Corpus           — 5 sources (SDK / OpenAPI / docs / blog / labs)
 *   2. Top-Orders       — 7 high-level capability areas
 *   3. Meta-Skills      — ~54 capability areas (Delta Lake, UC, etc.)
 *   4. Extensions       — fine-grained methods/operations under each Meta-Skill
 *   5. Refresh history  — last N indexer runs
 *
 * Tab state is persisted in the URL hash so links like
 * `/knowledge#top-orders` (used by the /catalog redirect) land on the
 * right tab. Every tab reads from `/api/knowledge/*` via the FastAPI
 * sidecar; the runtime returns empty payloads + a banner until the
 * indexer has produced an active snapshot — never fakes.
 *
 * C.1 SHELL: tab content is minimal (empty-state cards explaining
 * what each section will surface once the indexer runs). C.1 BULK
 * fills in the data tables, the provenance pane, and the search bar.
 */

const TABS = [
  { id: "corpus", label: "Corpus", Icon: Database },
  { id: "top-orders", label: "Top-Orders", Icon: Layers },
  { id: "meta-skills", label: "Meta-Skills", Icon: Boxes },
  { id: "extensions", label: "Extensions", Icon: ListTree },
  { id: "refresh-history", label: "Refresh history", Icon: Loader2 },
] as const;

type TabId = (typeof TABS)[number]["id"];

export const Route = createFileRoute("/knowledge")({
  component: KnowledgePage,
});

function KnowledgePage() {
  const initial = (
    typeof window !== "undefined"
      ? window.location.hash.replace("#", "")
      : ""
  ) as TabId;
  const [tab, setTab] = useState<TabId>(
    TABS.some((t) => t.id === initial) ? initial : "top-orders",
  );

  // N178 — right-rail provenance pane state (extension_id of the
  // currently-open pane, or null when closed). Kept lifted at the
  // page level so neighbour clicks inside the pane can swap the open
  // extension without round-tripping through the cards.
  const [openExtensionId, setOpenExtensionId] = useState<string | null>(null);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Knowledge</h1>
        <p className="text-sm text-muted-foreground">
          The Databricks Capability Graph: a nightly-indexed 3-level
          taxonomy of <strong>Top-Orders</strong> &gt;{" "}
          <strong>Meta-Skills</strong> &gt; <strong>Extensions</strong>{" "}
          derived from the Databricks SDK + REST OpenAPI + public docs
          + databricks.com/blog + Lakebridge labs. Replay-pinned via
          the <code className="font-mono text-xs">capability_graph_snapshot_id</code>{" "}
          contract. See{" "}
          <code className="font-mono text-xs">
            docs/23-databricks-capability-graph.md
          </code>
          .
        </p>
      </header>

      <KnowledgeSearchBar />

      <KnowledgeAskPanel />

      <KnowledgeHealthBanner />

      <CapabilityExplorer
        onOpenTopOrders={() => {
          setTab("top-orders");
          if (typeof window !== "undefined") {
            window.history.replaceState(null, "", "#top-orders");
          }
        }}
      />

      <nav
        className="flex flex-wrap gap-1 border-b border-border"
        role="tablist"
      >
        {TABS.map(({ id, label, Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => {
                setTab(id);
                if (typeof window !== "undefined") {
                  window.history.replaceState(null, "", `#${id}`);
                }
              }}
              className={cn(
                "inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon
                className={cn("h-4 w-4", active && "text-primary")}
                aria-hidden="true"
              />
              {label}
            </button>
          );
        })}
      </nav>

      <ErrorBoundary>
        <Suspense fallback={<PanelSkeleton />}>
          <TabContent
            tab={tab}
            onOpenProvenance={setOpenExtensionId}
          />
        </Suspense>
      </ErrorBoundary>

      <ProvenancePane
        extensionId={openExtensionId}
        onClose={() => setOpenExtensionId(null)}
        onNavigateToNeighbor={(id) => setOpenExtensionId(id)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Semantic search bar
// ---------------------------------------------------------------------------

interface SearchResult {
  id: string;
  entity_id: string;
  entity_kind: string;
  chunk_text: string;
  meta_skill_id: string | null;
  top_order_id: string | null;
  source_url: string;
  score: number | null;
}

interface SearchPayload {
  results: SearchResult[];
  query: string;
}

function KnowledgeSearchBar() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const { data, isFetching } = useQuery({
    queryKey: ["knowledge", "search", submitted],
    queryFn: () =>
      fetchJson<SearchPayload>("/api/knowledge/search", {
        query: { q: submitted, limit: 8 },
      }),
    enabled: submitted.length > 0,
    staleTime: 120_000,
  });

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (query.trim()) setSubmitted(query.trim());
    },
    [query],
  );

  return (
    <div className="space-y-3">
      <form onSubmit={handleSubmit} className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the capability graph — e.g. 'Delta Live Tables pipeline' or 'Unity Catalog permissions'"
          className="w-full rounded-lg border border-border bg-background py-2.5 pl-10 pr-4 text-sm shadow-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
        />
        {isFetching && (
          <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
        )}
      </form>

      {data && data.results.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">
            {data.results.length} results for &ldquo;{data.query}&rdquo;
          </div>
          <div className="grid gap-2">
            {data.results.map((hit) => (
              <Card
                key={hit.id}
                className="overflow-hidden border-border/60 shadow-none transition-colors hover:border-primary/40"
              >
                <CardContent className="p-3">
                  <div className="flex items-start gap-2">
                    <span
                      className={cn(
                        "mt-0.5 inline-block shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                        hit.entity_kind === "extension"
                          ? "bg-blue-500/10 text-blue-600 dark:text-blue-400"
                          : hit.entity_kind === "docs_chunk"
                            ? "bg-green-500/10 text-green-600 dark:text-green-400"
                            : "bg-orange-500/10 text-orange-600 dark:text-orange-400",
                      )}
                    >
                      {hit.entity_kind.replace("_", " ")}
                    </span>
                    <div className="min-w-0 flex-1 space-y-1">
                      <p className="line-clamp-3 text-xs leading-relaxed text-foreground/90">
                        {hit.chunk_text.slice(0, 300)}
                        {hit.chunk_text.length > 300 ? "…" : ""}
                      </p>
                      <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                        {hit.meta_skill_id && (
                          <span className="font-mono">{hit.meta_skill_id}</span>
                        )}
                        {hit.score != null && (
                          <span>score {hit.score.toFixed(3)}</span>
                        )}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {data && data.results.length === 0 && submitted && (
        <p className="text-xs text-muted-foreground">
          No results for &ldquo;{data.query}&rdquo;
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RAG Ask panel (HippoRAG2)
// ---------------------------------------------------------------------------

interface AskResponse {
  answer: string;
  code?: string;
  sources: {
    entity_id: string;
    entity_kind: string;
    meta_skill_id: string;
    source_url: string;
    chunk_text_preview: string;
  }[];
  question: string;
  chunks_retrieved: number;
  context_expanded: number;
}

function KnowledgeAskPanel() {
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleAsk = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!question.trim()) return;
      setLoading(true);
      setExpanded(true);
      try {
        const data = await fetchJson<AskResponse>("/api/knowledge/ask", {
          method: "POST",
          body: JSON.stringify({ question: question.trim(), top_k: 8 }),
        });
        setResponse(data);
      } catch {
        setResponse({
          answer: "Failed to get a response. Please try again.",
          sources: [],
          question: question.trim(),
          chunks_retrieved: 0,
          context_expanded: 0,
        });
      } finally {
        setLoading(false);
      }
    },
    [question],
  );

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border/60 px-4 py-2.5 text-left text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
      >
        <Layers className="h-4 w-4" />
        Ask BrickVision — generates grounded code from the capability graph
      </button>
    );
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Layers className="h-4 w-4 text-primary" />
          Ask BrickVision
          <span className="text-xs font-normal text-muted-foreground">
            Generates grounded, runnable code from the capability graph
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={handleAsk} className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. 'Create a DLT pipeline with schema evolution' or 'Set up model serving with A/B testing'"
            className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            Ask
          </button>
        </form>

        {loading && (
          <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Retrieving from capability graph → multi-hop expansion → generating grounded code...
          </div>
        )}

        {response && !loading && (
          <div className="space-y-3">
            {/* Explanation */}
            <div className="rounded-lg border border-border/60 bg-muted/20 p-4">
              <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-sm leading-relaxed">
                {response.answer}
              </div>
            </div>

            {/* Generated Code */}
            {response.code && response.code.trim() && (
              <CodeBlock code={response.code} />
            )}

            {/* Sources */}
            {response.sources.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  Grounded in {response.chunks_retrieved} chunks, {response.context_expanded} with multi-hop graph expansion
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {response.sources.slice(0, 6).map((src, i) => (
                    <span
                      key={`${src.entity_id}-${i}`}
                      className={cn(
                        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium",
                        src.entity_kind === "extension"
                          ? "bg-blue-500/10 text-blue-600 dark:text-blue-400"
                          : src.entity_kind === "docs_chunk"
                            ? "bg-green-500/10 text-green-600 dark:text-green-400"
                            : "bg-orange-500/10 text-orange-600 dark:text-orange-400",
                      )}
                      title={src.chunk_text_preview}
                    >
                      {src.entity_kind.replace("_", " ")}
                      {src.meta_skill_id && (
                        <span className="font-mono opacity-60">
                          {src.meta_skill_id}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Code block with copy-to-clipboard
// ---------------------------------------------------------------------------

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className="group relative rounded-lg border border-border/60 bg-zinc-950 dark:bg-zinc-900">
      <div className="flex items-center justify-between border-b border-border/40 px-4 py-2">
        <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-400">
          Generated Code — grounded in capability graph
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
        >
          {copied ? (
            <>
              <ShieldCheck className="h-3 w-3" />
              Copied
            </>
          ) : (
            <>
              <Globe2 className="h-3 w-3" />
              Copy
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 text-[12px] leading-relaxed text-zinc-200">
        <code>{code}</code>
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Health banner
// ---------------------------------------------------------------------------

interface IndexerBanner {
  active_snapshot_id: string | null;
  freshness_days: number | null;
  freshness_tolerance_days: number;
  is_stale: boolean;
  is_missing: boolean;
  smoke_baseline_pass_rate: number | null;
  criterion_13_status: string;
  partial_sources: string[];
  indexer_state?: string;
  message?: string;
}

function KnowledgeHealthBanner() {
  const { data } = useQuery({
    queryKey: ["knowledge", "health"],
    queryFn: () => fetchJson<IndexerBanner>("/api/knowledge/health"),
    staleTime: 60_000,
  });

  if (!data) return null;
  if (!data.is_missing && !data.is_stale && !data.partial_sources.length) {
    return null;
  }

  const tone = data.is_missing
    ? "border-amber-500/40 bg-amber-500/5 text-amber-900 dark:text-amber-200"
    : "border-orange-500/40 bg-orange-500/5";

  return (
    <Card className={cn("border-l-4", tone)}>
      <CardContent className="flex items-start gap-3 py-3 text-sm">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 shrink-0"
          aria-hidden="true"
        />
        <div className="space-y-1">
          {data.is_missing && (
            <div className="font-medium">
              Capability indexer has not yet produced an active snapshot.
            </div>
          )}
          {data.is_stale && (
            <div className="font-medium">
              Capability graph is stale (older than{" "}
              {data.freshness_tolerance_days} days).
            </div>
          )}
          {data.partial_sources.length > 0 && (
            <div className="font-medium">
              Partial sources in the active snapshot:{" "}
              <code className="font-mono text-xs">
                {data.partial_sources.join(", ")}
              </code>
            </div>
          )}
          {data.message && (
            <div className="text-muted-foreground">{data.message}</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Capability Explorer
// ---------------------------------------------------------------------------

function CapabilityExplorer({ onOpenTopOrders }: { onOpenTopOrders: () => void }) {
  const { data: topOrders = [] } = useQuery({
    queryKey: ["knowledge", "top-orders"],
    queryFn: () => fetchJson<TopOrder[]>("/api/knowledge/top-orders"),
    staleTime: 60_000,
  });
  const { data: metaSkills = [] } = useQuery({
    queryKey: ["knowledge", "meta-skills"],
    queryFn: () => fetchJson<MetaSkill[]>("/api/knowledge/meta-skills"),
    staleTime: 60_000,
  });

  const topOrderRows = useMemo(
    () =>
      topOrders.map((topOrder) => {
        const children = metaSkills.filter(
          (metaSkill) => metaSkill.parent_top_order === topOrder.top_order_id,
        );
        const covered = children.filter(
          (metaSkill) => metaSkill.hand_authored_exemplar_count > 0,
        ).length;
        const discovered = children.filter(
          (metaSkill) => metaSkill.extension_count > 0,
        ).length;
        return {
          ...topOrder,
          children,
          covered,
          discovered,
          coverageRate: children.length ? covered / children.length : 0,
        };
      }),
    [metaSkills, topOrders],
  );

  if (!topOrderRows.length) {
    return null;
  }

  const totalMetaSkills = metaSkills.length;
  const totalCovered = metaSkills.filter(
    (metaSkill) => metaSkill.hand_authored_exemplar_count > 0,
  ).length;
  const totalExtensions = topOrders.reduce(
    (sum, topOrder) => sum + topOrder.extension_count,
    0,
  );

  return (
    <Card className="overflow-hidden border-border/70">
      <CardHeader className="border-b bg-muted/20">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              Capability Explorer
              <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-muted-foreground">
                Top-Order → Meta-Skill → Extension
              </span>
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              A heatmap-style view of where BrickVision has Databricks capability
              coverage, exemplar-backed skills, and areas that still need validation.
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenTopOrders}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm transition-colors hover:bg-accent"
          >
            Open detailed summary
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 p-4">
        <div className="grid gap-3 md:grid-cols-3">
          <ExplorerMetric label="Meta-skills" value={String(totalMetaSkills)} />
          <ExplorerMetric label="Extensions" value={String(totalExtensions)} />
          <ExplorerMetric
            label="Exemplar coverage"
            value={formatExplorerPercent(totalCovered, totalMetaSkills)}
          />
        </div>

        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          <ExplorerLegend swatch="bg-emerald-500" label="exemplar-backed" />
          <ExplorerLegend swatch="bg-violet-500" label="extension-rich" />
          <ExplorerLegend swatch="bg-amber-500" label="discovered, needs exemplar" />
          <ExplorerLegend swatch="bg-muted" label="empty or not yet indexed" />
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          {topOrderRows.map((topOrder) => (
            <div
              key={topOrder.top_order_id}
              className="rounded-xl border border-border/70 bg-card p-4 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">
                    {topOrder.label}
                  </div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">
                    {topOrder.top_order_id}
                  </div>
                </div>
                <div className="rounded-full bg-muted px-2 py-1 text-xs font-medium">
                  {Math.round(topOrder.coverageRate * 100)}%
                </div>
              </div>

              <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
                <MiniStat label="meta" value={topOrder.children.length} />
                <MiniStat label="ext" value={topOrder.extension_count} />
                <MiniStat label="covered" value={topOrder.covered} />
              </div>

              <div className="grid grid-cols-9 gap-1.5">
                {topOrder.children.map((metaSkill) => (
                  <MetaSkillSwatch key={metaSkill.meta_skill_id} metaSkill={metaSkill} />
                ))}
                {topOrder.children.length === 0 ? (
                  <div className="col-span-9 rounded-md bg-muted p-3 text-xs text-muted-foreground">
                    No meta-skills indexed yet.
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function MetaSkillSwatch({ metaSkill }: { metaSkill: MetaSkill }) {
  return (
    <div className="group relative">
      <div
        className={cn(
          "h-5 rounded-[4px] border border-background shadow-sm transition-transform group-hover:scale-125",
          metaSkillCoverageClass(metaSkill),
        )}
      />
      <div className="pointer-events-none absolute left-1/2 top-7 z-30 hidden w-72 -translate-x-1/2 rounded-xl border border-border bg-background p-3 text-xs shadow-xl group-hover:block">
        <div className="font-semibold">{metaSkill.label}</div>
        <div className="mt-1 font-mono text-[10px] text-muted-foreground">
          {metaSkill.meta_skill_id}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <MiniStat label="extensions" value={metaSkill.extension_count} />
          <MiniStat
            label="exemplars"
            value={metaSkill.hand_authored_exemplar_count}
          />
        </div>
        <p className="mt-3 text-muted-foreground">
          {metaSkill.hand_authored_exemplar_count > 0
            ? "This capability area has at least one exemplar-backed Extension."
            : metaSkill.extension_count > 0
              ? "This capability has been discovered, but still needs exemplar-backed validation."
              : "This capability is known but has no indexed Extensions yet."}
        </p>
      </div>
    </div>
  );
}

function ExplorerMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-background p-3">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function ExplorerLegend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={cn("h-3 w-3 rounded-sm", swatch)} />
      {label}
    </span>
  );
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md bg-muted/60 px-2 py-1">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-mono font-semibold">{value}</div>
    </div>
  );
}

function metaSkillCoverageClass(metaSkill: MetaSkill): string {
  if (metaSkill.hand_authored_exemplar_count > 0) {
    return "bg-emerald-500";
  }
  if (metaSkill.extension_count >= 20) {
    return "bg-violet-500";
  }
  if (metaSkill.extension_count > 0) {
    return "bg-amber-500";
  }
  return "bg-muted";
}

function formatExplorerPercent(numerator: number, denominator: number): string {
  if (!denominator) {
    return "0%";
  }
  return `${Math.round((numerator / denominator) * 100)}%`;
}

function TabContent({
  tab,
  onOpenProvenance,
}: {
  tab: TabId;
  onOpenProvenance: (extensionId: string) => void;
}) {
  switch (tab) {
    case "corpus":
      return <CorpusTab />;
    case "top-orders":
      return <TopOrdersTab />;
    case "meta-skills":
      return <MetaSkillsTab />;
    case "extensions":
      return <ExtensionsTab onOpenProvenance={onOpenProvenance} />;
    case "refresh-history":
      return <RefreshHistoryTab />;
  }
}

interface CorpusPayload {
  sources: Array<{
    source_id: string;
    url_root: string;
    source_authority: number;
    last_refresh_ts: string | null;
    state: string;
    extension_count: number;
  }>;
  active_snapshot_id?: string;
  promoted_at_ms?: number;
  indexer_state: string;
  message?: string;
}

function CorpusTab() {
  const { data } = useQuery({
    queryKey: ["knowledge", "corpus"],
    queryFn: () => fetchJson<CorpusPayload>("/api/knowledge/corpus"),
    staleTime: 60_000,
  });
  if (!data) return <PanelSkeleton />;
  if (!data.sources.length) {
    return (
      <EmptyState
        title="Corpus has not yet been indexed."
        body="Once the indexer runs, this tab lists 5 sources: databricks-sdk-py (authority 1.0), Databricks REST OpenAPI (0.9), docs.databricks.com + learn.microsoft.com (0.7), databricks.com/blog (0.5), github.com/databrickslabs/lakebridge (0.6) — each with state, freshness, and contributing-extension count."
      />
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
      {data.sources.map((s) => (
        <Card key={s.source_id}>
          <CardHeader>
            <CardTitle className="text-base">{s.source_id}</CardTitle>
            <div className="font-mono text-[10px] text-muted-foreground">
              {s.url_root}
            </div>
          </CardHeader>
          <CardContent className="space-y-1 text-xs text-muted-foreground">
            <div>
              authority{" "}
              <span className="font-mono text-foreground">
                {s.source_authority}
              </span>
            </div>
            <div>
              state{" "}
              <span className="font-mono text-foreground">{s.state}</span>
            </div>
            <div>
              extensions{" "}
              <span className="font-mono text-foreground">
                {s.extension_count}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

interface TopOrder {
  top_order_id: string;
  label: string;
  meta_skill_count: number;
  extension_count: number;
  hand_authored_exemplar_count: number;
}

function TopOrdersTab() {
  const { data = [] } = useQuery({
    queryKey: ["knowledge", "top-orders"],
    queryFn: () => fetchJson<TopOrder[]>("/api/knowledge/top-orders"),
    staleTime: 60_000,
  });
  if (data.length === 0) {
    return (
      <EmptyState
        title="Top-Orders not yet available."
        body="The 7 v0.7.7 Top-Orders ship as: Data Architecture Design, Data Engineering Design, Data Modelling Automation Design, AI Agent Design / Harness Engineering, Migration & Ingestion, ML Lifecycle, Governance & FinOps. Each will show its Meta-Skill count, Extension count, and hand-authored exemplar coverage badge once the indexer has produced a snapshot."
      />
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {data.map((to) => (
        <Card key={to.top_order_id}>
          <CardHeader>
            <CardTitle className="text-base">{to.label}</CardTitle>
            <div className="font-mono text-[10px] text-muted-foreground">
              {to.top_order_id}
            </div>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            <div>
              meta-skills{" "}
              <span className="font-mono text-foreground">
                {to.meta_skill_count}
              </span>{" "}
              · extensions{" "}
              <span className="font-mono text-foreground">
                {to.extension_count}
              </span>{" "}
              · hand-authored exemplars{" "}
              <span className="font-mono text-foreground">
                {to.hand_authored_exemplar_count}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

interface MetaSkill {
  meta_skill_id: string;
  label: string;
  parent_top_order: string;
  extension_count: number;
  hand_authored_exemplar_count: number;
}

function MetaSkillsTab() {
  const { data = [] } = useQuery({
    queryKey: ["knowledge", "meta-skills"],
    queryFn: () => fetchJson<MetaSkill[]>("/api/knowledge/meta-skills"),
    staleTime: 60_000,
  });
  if (data.length === 0) {
    return (
      <EmptyState
        title="Meta-Skills not yet available."
        body="~54 Meta-Skills are expected at v0.7.7 ship: Delta Lake, Unity Catalog, MLflow Tracking, Mosaic AI Model Serving, Lakeflow Declarative Pipelines, etc. Each is derived empirically from databricks-sdk module names + docs section roots; see docs/23-databricks-capability-graph.md §23.2.5 for the published list."
      />
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
      {data.map((m) => (
        <Card key={m.meta_skill_id}>
          <CardHeader>
            <CardTitle className="text-base">{m.label}</CardTitle>
            <div className="font-mono text-[10px] text-muted-foreground">
              {m.meta_skill_id}
            </div>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            <div>
              under{" "}
              <span className="font-mono text-foreground">
                {m.parent_top_order}
              </span>
            </div>
            <div>
              extensions{" "}
              <span className="font-mono text-foreground">
                {m.extension_count}
              </span>{" "}
              · hand-authored{" "}
              <span className="font-mono text-foreground">
                {m.hand_authored_exemplar_count}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

interface Extension {
  extension_id: string;
  label: string;
  parent_meta_skill: string;
  effect_class: string;
  cloud_variance: string;
  has_exemplar: boolean;
  exemplar_skill_id: string | null;
}

function ExtensionsTab({
  onOpenProvenance,
}: {
  onOpenProvenance: (extensionId: string) => void;
}) {
  const { data = [] } = useQuery({
    queryKey: ["knowledge", "extensions"],
    queryFn: () => fetchJson<Extension[]>("/api/knowledge/extensions"),
    staleTime: 60_000,
  });
  if (data.length === 0) {
    return (
      <EmptyState
        title="Extensions not yet available."
        body="Extensions are fine-grained Databricks capabilities under each Meta-Skill: e.g. ext:introspect-table-metadata under meta:delta-lake. Derived from SDK methods, REST operations, and docs sections. The 15 hand-authored Layer-0 skills currently on disk become 'named exemplar Extensions' when the indexer first runs (see docs/23-databricks-capability-graph.md §23.2.6)."
      />
    );
  }
  return (
    <div className="space-y-2">
      {data.map((e) => (
        <Card
          key={e.extension_id}
          onClick={() => onOpenProvenance(e.extension_id)}
          role="button"
          tabIndex={0}
          aria-label={`Open provenance pane for ${e.extension_id}`}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onOpenProvenance(e.extension_id);
            }
          }}
          className="cursor-pointer transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <CardContent className="flex items-start justify-between gap-4 py-3 text-xs">
            <div className="space-y-1">
              <div className="font-medium text-sm">{e.label}</div>
              <div className="font-mono text-[10px] text-muted-foreground">
                {e.extension_id} · {e.parent_meta_skill}
              </div>
            </div>
            <div className="space-y-1 text-right text-muted-foreground">
              <div>
                effect{" "}
                <span className="font-mono text-foreground">
                  {e.effect_class}
                </span>
              </div>
              {e.has_exemplar && e.exemplar_skill_id && (
                <div>
                  exemplar{" "}
                  <span className="font-mono text-foreground">
                    {e.exemplar_skill_id}
                  </span>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

interface RefreshRow {
  run_id: string;
  started_at_ms: number;
  ended_at_ms: number | null;
  snapshot_id: string;
  state: string;
  rejection_reason_code: string | null;
  partial_sources: string[];
  total_input_tokens: number;
}

function RefreshHistoryTab() {
  const { data = [] } = useQuery({
    queryKey: ["knowledge", "refresh-history"],
    queryFn: () => fetchJson<RefreshRow[]>("/api/knowledge/refresh-history"),
    staleTime: 60_000,
  });
  if (data.length === 0) {
    return (
      <EmptyState
        title="No refresh history yet."
        body="The capability indexer is a multi-task serverless Databricks Job (bv_capability_indexer) that runs nightly under bv_indexer_sp. Each run produces a corpus_snapshot row; promotion is atomic via the singleton active_snapshot_id table. Once a refresh has completed, this tab shows its run_id, snapshot_id, state (ok | rejected | partial), rejection reason code (if any), partial sources, and token spend."
      />
    );
  }
  return (
    <div className="space-y-2">
      {data.map((r) => (
        <Card key={r.run_id}>
          <CardContent className="flex items-start justify-between gap-4 py-3 text-xs">
            <div className="space-y-1">
              <div className="font-mono text-sm font-medium">
                {r.snapshot_id}
              </div>
              <div className="font-mono text-[10px] text-muted-foreground">
                run_id={r.run_id}
              </div>
            </div>
            <div className="space-y-1 text-right text-muted-foreground">
              <div>
                state{" "}
                <span className="font-mono text-foreground">{r.state}</span>
              </div>
              {r.rejection_reason_code && (
                <div className="font-mono text-foreground">
                  {r.rejection_reason_code}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// N178 — Right-rail provenance pane.
// ---------------------------------------------------------------------------
//
// Per docs/21-roadmap.md §19.16 N178: "Right-rail pane (or modal on
// narrow viewports) shown on extension click; source URL · file +
// line · commit SHA · parsed-at · signed-by · authority score +
// scorer · 2-hop graph view (clickable) · cross-cloud note (when
// cloud_variance ≠ invariant)." The pane reads
// /api/knowledge/extensions/{extension_id}/provenance and renders
// the structured payload returned by the runtime bridge — which is
// shape-stable whether or not the indexer has produced an active
// snapshot, so the pane never crashes on a bootstrap install (it
// renders an "indexer not yet run" notice instead).

interface ProvenanceChunk {
  source_id: string;
  source_url: string | null;
  file_path: string | null;
  line_start: number | null;
  line_end: number | null;
  commit_sha: string | null;
  parsed_at_ms: number | null;
  signed_by: string | null;
  authority_score: number | null;
  scorer: string | null;
}

interface ProvenanceNeighbor {
  extension_id: string;
  label: string | null;
  relation: string;
  hop: 1 | 2;
}

interface ExtensionProvenance {
  extension_id: string;
  label: string | null;
  parent_meta_skill: string | null;
  effect_class: string | null;
  cloud_variance: string | null;
  authority_score: number | null;
  authority_scorer: string | null;
  cross_cloud_note: string | null;
  contributing_chunks: ProvenanceChunk[];
  two_hop_neighbors: ProvenanceNeighbor[];
  active_snapshot_id?: string | null;
  promoted_at_ms?: number | null;
  indexer_state?: string;
  message?: string;
}

function ProvenancePane({
  extensionId,
  onClose,
  onNavigateToNeighbor,
}: {
  extensionId: string | null;
  onClose: () => void;
  onNavigateToNeighbor: (extensionId: string) => void;
}) {
  // Escape closes the pane (a11y).
  useEffect(() => {
    if (!extensionId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [extensionId, onClose]);

  if (!extensionId) return null;

  return (
    <>
      <button
        type="button"
        aria-label="Close provenance pane"
        onClick={onClose}
        className="fixed inset-0 z-40 bg-background/40 backdrop-blur-sm"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Provenance pane for ${extensionId}`}
        className={cn(
          "fixed inset-y-0 right-0 z-50 w-full overflow-y-auto border-l border-border bg-background shadow-xl",
          "max-w-full sm:max-w-md lg:max-w-lg",
        )}
      >
        <ErrorBoundary>
          <Suspense fallback={<PanelSkeleton />}>
            <ProvenancePaneBody
              extensionId={extensionId}
              onClose={onClose}
              onNavigateToNeighbor={onNavigateToNeighbor}
            />
          </Suspense>
        </ErrorBoundary>
      </aside>
    </>
  );
}

function ProvenancePaneBody({
  extensionId,
  onClose,
  onNavigateToNeighbor,
}: {
  extensionId: string;
  onClose: () => void;
  onNavigateToNeighbor: (extensionId: string) => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["knowledge", "provenance", extensionId],
    queryFn: () =>
      fetchJson<ExtensionProvenance>("/api/knowledge/extensions/provenance", {
        query: { extension_id: extensionId },
      }),
    staleTime: 60_000,
  });

  return (
    <div className="flex h-full flex-col">
      <header className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-border bg-background/95 px-5 py-4 backdrop-blur">
        <div className="space-y-1">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Provenance
          </div>
          <div className="font-mono text-sm font-medium">{extensionId}</div>
          {data?.label && (
            <div className="text-xs text-muted-foreground">{data.label}</div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Close"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </header>

      {isLoading && <div className="px-5 py-6"><PanelSkeleton /></div>}

      {isError && (
        <div className="space-y-2 px-5 py-6 text-sm">
          <AlertTriangle
            className="h-4 w-4 text-destructive"
            aria-hidden="true"
          />
          <div>Could not load provenance for this extension.</div>
        </div>
      )}

      {data && (
        <div className="space-y-5 px-5 py-5 text-sm">
          {data.indexer_state && data.indexer_state !== "active" && (
            <div className="rounded border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-amber-900 dark:text-amber-200">
              <div className="flex items-start gap-2">
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  aria-hidden="true"
                />
                <div>
                  <div className="font-medium">Indexer has not yet run.</div>
                  {data.message && (
                    <div className="mt-1 text-muted-foreground">
                      {data.message}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <ProvenanceIdentitySection data={data} />

          <ProvenanceAuthoritySection data={data} />

          {data.cross_cloud_note && (
            <CrossCloudNoteSection
              cloudVariance={data.cloud_variance}
              note={data.cross_cloud_note}
            />
          )}

          <ProvenanceChunksSection chunks={data.contributing_chunks} />

          <ProvenanceNeighborsSection
            neighbors={data.two_hop_neighbors}
            onNavigate={onNavigateToNeighbor}
            centerLabel={data.extension_id}
          />

          {data.active_snapshot_id && (
            <div className="rounded border border-border/60 bg-muted/40 px-3 py-2 text-[10px] text-muted-foreground">
              <div>
                snapshot{" "}
                <span className="font-mono text-foreground">
                  {data.active_snapshot_id}
                </span>
              </div>
              {data.promoted_at_ms && (
                <div>
                  promoted_at_ms{" "}
                  <span className="font-mono text-foreground">
                    {data.promoted_at_ms}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProvenanceIdentitySection({ data }: { data: ExtensionProvenance }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Identity
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">parent meta-skill</dt>
        <dd className="font-mono">
          {data.parent_meta_skill ?? <Placeholder />}
        </dd>
        <dt className="text-muted-foreground">effect class</dt>
        <dd className="font-mono">{data.effect_class ?? <Placeholder />}</dd>
        <dt className="text-muted-foreground">cloud variance</dt>
        <dd className="font-mono">{data.cloud_variance ?? <Placeholder />}</dd>
      </dl>
    </section>
  );
}

function ProvenanceAuthoritySection({ data }: { data: ExtensionProvenance }) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
        Authority
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">authority score</dt>
        <dd className="font-mono">
          {data.authority_score === null || data.authority_score === undefined
            ? <Placeholder />
            : data.authority_score}
        </dd>
        <dt className="text-muted-foreground">scorer</dt>
        <dd className="font-mono">
          {data.authority_scorer ?? <Placeholder />}
        </dd>
      </dl>
    </section>
  );
}

function CrossCloudNoteSection({
  cloudVariance,
  note,
}: {
  cloudVariance: string | null;
  note: string;
}) {
  return (
    <section className="rounded border border-blue-500/40 bg-blue-500/5 p-3 text-xs">
      <div className="flex items-start gap-2">
        <Globe2
          className="mt-0.5 h-4 w-4 shrink-0 text-blue-500"
          aria-hidden="true"
        />
        <div className="space-y-1">
          <div className="font-medium">
            Cross-cloud:{" "}
            <span className="font-mono">{cloudVariance ?? "unknown"}</span>
          </div>
          <div className="text-muted-foreground">{note}</div>
        </div>
      </div>
    </section>
  );
}

function ProvenanceChunksSection({
  chunks,
}: {
  chunks: ProvenanceChunk[];
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Contributing chunks ({chunks.length})
      </h3>
      {chunks.length === 0 ? (
        <div className="rounded border border-dashed border-border/60 px-3 py-3 text-xs text-muted-foreground">
          No contributing chunks recorded yet.
        </div>
      ) : (
        <ul className="space-y-2">
          {chunks.map((chunk, i) => (
            <li
              key={`${chunk.source_id}-${i}`}
              className="space-y-1 rounded border border-border/60 px-3 py-2 text-[11px]"
            >
              <div className="flex items-center justify-between font-mono">
                <span className="font-medium">{chunk.source_id}</span>
                {chunk.authority_score !== null && (
                  <span className="text-muted-foreground">
                    auth{" "}
                    <span className="text-foreground">
                      {chunk.authority_score}
                    </span>
                  </span>
                )}
              </div>
              {chunk.source_url && (
                <a
                  href={chunk.source_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="block break-all font-mono text-blue-500 hover:underline"
                >
                  {chunk.source_url}
                </a>
              )}
              {chunk.file_path && (
                <div className="font-mono text-muted-foreground">
                  {chunk.file_path}
                  {chunk.line_start !== null &&
                    `:${chunk.line_start}`}
                  {chunk.line_end !== null && chunk.line_end !== chunk.line_start &&
                    `-${chunk.line_end}`}
                </div>
              )}
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-[10px]">
                {chunk.commit_sha && (
                  <>
                    <dt className="text-muted-foreground">commit</dt>
                    <dd className="font-mono">{chunk.commit_sha.slice(0, 12)}</dd>
                  </>
                )}
                {chunk.parsed_at_ms && (
                  <>
                    <dt className="text-muted-foreground">parsed_at_ms</dt>
                    <dd className="font-mono">{chunk.parsed_at_ms}</dd>
                  </>
                )}
                {chunk.signed_by && (
                  <>
                    <dt className="text-muted-foreground">signed_by</dt>
                    <dd className="font-mono">{chunk.signed_by}</dd>
                  </>
                )}
                {chunk.scorer && (
                  <>
                    <dt className="text-muted-foreground">scorer</dt>
                    <dd className="font-mono">{chunk.scorer}</dd>
                  </>
                )}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ProvenanceNeighborsSection({
  neighbors,
  onNavigate,
  centerLabel,
}: {
  neighbors: ProvenanceNeighbor[];
  onNavigate: (extensionId: string) => void;
  centerLabel: string;
}) {
  const maxHop = Math.max(...neighbors.map((n) => n.hop), 0);
  const hopGroups = Array.from({ length: maxHop }, (_, i) =>
    neighbors.filter((n) => n.hop === i + 1),
  );

  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {maxHop > 0 ? `${maxHop}-hop` : "Multi-hop"} graph ({neighbors.length})
      </h3>
      {neighbors.length === 0 ? (
        <div className="rounded border border-dashed border-border/60 px-3 py-3 text-xs text-muted-foreground">
          No neighbours recorded yet.
        </div>
      ) : (
        <>
          <GraphVisualization
            centerLabel={centerLabel}
            hopGroups={hopGroups}
            onNavigate={onNavigate}
          />
          <div className="space-y-3">
            {hopGroups.map((group, i) =>
              group.length > 0 ? (
                <NeighborGroup
                  key={i}
                  label={`${i + 1}-hop`}
                  neighbors={group}
                  onNavigate={onNavigate}
                />
              ) : null,
            )}
          </div>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// N178 — Interactive radial SVG multi-hop graph visualization
// ---------------------------------------------------------------------------

const GRAPH_SIZE = 420;
const CENTER = GRAPH_SIZE / 2;
const BASE_RADIUS = 70;
const RING_SPACING = 55;

const RELATION_COLORS: Record<string, string> = {
  mentions: "#22c55e",
  cites_sdk: "#3b82f6",
  derives: "#a855f7",
  deprecates: "#ef4444",
  sibling: "#f59e0b",
  cites: "#06b6d4",
};

function getRelationColor(relation: string): string {
  return RELATION_COLORS[relation] ?? "#6b7280";
}

function polarToXY(cx: number, cy: number, radius: number, angleRad: number) {
  return {
    x: cx + radius * Math.cos(angleRad),
    y: cy + radius * Math.sin(angleRad),
  };
}

function truncateId(id: string, maxLen = 18): string {
  const short = id.replace(/^(meta:|ext:|to:)/, "");
  return short.length > maxLen ? short.slice(0, maxLen - 1) + "…" : short;
}

function GraphVisualization({
  centerLabel,
  hopGroups,
  onNavigate,
}: {
  centerLabel: string;
  hopGroups: ProvenanceNeighbor[][];
  onNavigate: (extensionId: string) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  const ringRadii = hopGroups.map((_, i) => BASE_RADIUS + RING_SPACING * i);

  const groupPositions = hopGroups.map((group, ringIdx) =>
    group.map((_, i) => {
      const angle = (2 * Math.PI * i) / Math.max(group.length, 1) - Math.PI / 2;
      return polarToXY(CENTER, CENTER, ringRadii[ringIdx] ?? BASE_RADIUS, angle);
    }),
  );

  const hopOpacity = (hop: number) => Math.max(0.25, 1 - hop * 0.2);
  const nodeRadius = (hop: number, highlighted: boolean) => {
    const base = Math.max(4, 9 - hop * 1.5);
    return highlighted ? base + 2 : base;
  };

  return (
    <div className="overflow-hidden rounded-lg border border-border/60 bg-muted/20">
      <svg
        viewBox={`0 0 ${GRAPH_SIZE} ${GRAPH_SIZE}`}
        className="w-full"
        style={{ maxHeight: 360 }}
      >
        {/* Orbit rings */}
        {ringRadii.map((r, i) => (
          <circle
            key={`ring-${i}`}
            cx={CENTER}
            cy={CENTER}
            r={r}
            fill="none"
            stroke="currentColor"
            className="text-border/30"
            strokeWidth={1}
            strokeDasharray={i === 0 ? "4 4" : "2 4"}
            strokeOpacity={hopOpacity(i)}
          />
        ))}

        {/* Edges — connect each ring to its parent ring */}
        {hopGroups.map((group, ringIdx) =>
          group.map((n, nodeIdx) => {
            const pos = groupPositions[ringIdx]?.[nodeIdx] ?? { x: CENTER, y: CENTER };
            const isHighlighted = hovered === n.extension_id;
            let parentPos = { x: CENTER, y: CENTER };
            const parentRing = groupPositions[ringIdx - 1] ?? [];
            if (ringIdx > 0 && parentRing.length > 0) {
              const parentIdx = nodeIdx % parentRing.length;
              parentPos = parentRing[parentIdx] ?? parentPos;
            }
            return (
              <line
                key={`edge-${ringIdx}-${nodeIdx}-${n.extension_id}`}
                x1={parentPos.x}
                y1={parentPos.y}
                x2={pos.x}
                y2={pos.y}
                stroke={getRelationColor(n.relation)}
                strokeWidth={isHighlighted ? 2 : Math.max(0.5, 1.2 - ringIdx * 0.3)}
                strokeOpacity={isHighlighted ? 0.9 : 0.3 + (1 - ringIdx / hopGroups.length) * 0.2}
              />
            );
          }),
        )}

        {/* Nodes — render outer rings first so inner ones overlay */}
        {[...hopGroups].reverse().map((group, revIdx) => {
          const ringIdx = hopGroups.length - 1 - revIdx;
          return group.map((n, nodeIdx) => {
            const pos = groupPositions[ringIdx]?.[nodeIdx] ?? { x: CENTER, y: CENTER };
            const isHighlighted = hovered === n.extension_id;
            const r = nodeRadius(ringIdx, isHighlighted);
            return (
              <g
                key={`node-${ringIdx}-${nodeIdx}-${n.extension_id}`}
                className="cursor-pointer"
                onClick={() => onNavigate(n.extension_id)}
                onMouseEnter={() => setHovered(n.extension_id)}
                onMouseLeave={() => setHovered(null)}
              >
                <circle
                  cx={pos.x}
                  cy={pos.y}
                  r={r}
                  fill={getRelationColor(n.relation)}
                  fillOpacity={isHighlighted ? 1 : hopOpacity(ringIdx)}
                  stroke={isHighlighted ? "white" : "none"}
                  strokeWidth={1.5}
                />
                {(isHighlighted || ringIdx === 0) && (
                  <text
                    x={pos.x}
                    y={pos.y + (pos.y < CENTER ? -(r + 4) : r + 10)}
                    textAnchor="middle"
                    className="fill-foreground/80 text-[8px] font-mono"
                  >
                    {truncateId(n.extension_id)}
                  </text>
                )}
                {isHighlighted && (
                  <text
                    x={pos.x}
                    y={pos.y + (pos.y < CENTER ? -(r - 4) : r + 18)}
                    textAnchor="middle"
                    className="fill-muted-foreground text-[7px]"
                  >
                    {n.relation} (hop {ringIdx + 1})
                  </text>
                )}
              </g>
            );
          });
        })}

        {/* Center node */}
        <circle
          cx={CENTER}
          cy={CENTER}
          r={12}
          className="fill-primary"
          strokeWidth={3}
          stroke="white"
          strokeOpacity={0.3}
        />
        <text
          x={CENTER}
          y={CENTER + 22}
          textAnchor="middle"
          className="fill-foreground text-[9px] font-mono font-semibold"
        >
          {truncateId(centerLabel, 22)}
        </text>
      </svg>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-border/40 px-3 py-2">
        {Object.entries(RELATION_COLORS).map(([rel, color]) => (
          <div key={rel} className="flex items-center gap-1 text-[9px] text-muted-foreground">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: color }}
            />
            {rel}
          </div>
        ))}
        <div className="ml-auto text-[9px] text-muted-foreground/60">
          {hopGroups.length} hop{hopGroups.length !== 1 ? "s" : ""} depth
        </div>
      </div>
    </div>
  );
}

function NeighborGroup({
  label,
  neighbors,
  onNavigate,
}: {
  label: string;
  neighbors: ProvenanceNeighbor[];
  onNavigate: (extensionId: string) => void;
}) {
  if (neighbors.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="flex flex-wrap gap-1">
        {neighbors.map((n) => (
          <button
            key={n.extension_id + n.relation}
            type="button"
            onClick={() => onNavigate(n.extension_id)}
            className="inline-flex items-center gap-1 rounded border border-border/60 bg-muted/40 px-2 py-1 font-mono text-[10px] hover:bg-muted"
            aria-label={`Open provenance for ${n.extension_id}`}
          >
            <span className="text-muted-foreground">{n.relation}</span>
            <span>{n.extension_id}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Placeholder() {
  return <span className="text-muted-foreground/60">—</span>;
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <Card>
      <CardContent className="space-y-2 py-10 text-center text-sm">
        <div className="font-medium">{title}</div>
        <div className="text-muted-foreground">{body}</div>
        <div className="text-muted-foreground">
          To trigger a refresh:{" "}
          <code className="font-mono text-xs">
            brickvision indexer refresh
          </code>{" "}
          (see{" "}
          <code className="font-mono text-xs">
            docs/19-local-development.md
          </code>{" "}
          §15.6).
        </div>
      </CardContent>
    </Card>
  );
}
