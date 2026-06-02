import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Search } from "lucide-react";

import { fetchJson } from "@/lib/api";
import { cn } from "@/lib/utils";

interface SearchHit {
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
  results: SearchHit[];
  query: string;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function handler(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
    }
    function openHandler() {
      setOpen(true);
    }
    window.addEventListener("keydown", handler);
    window.addEventListener("brickvision:open-cmdk", openHandler);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("brickvision:open-cmdk", openHandler);
    };
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }
    setLoading(true);
    try {
      const data = await fetchJson<SearchPayload>("/api/knowledge/search", {
        query: { q: q.trim(), limit: 8 },
      });
      setResults(data.results);
      setSearched(true);
    } catch {
      setResults([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleChange = useCallback(
    (value: string) => {
      setQuery(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => doSearch(value), 400);
    },
    [doSearch],
  );

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (debounceRef.current) clearTimeout(debounceRef.current);
      doSearch(query);
    },
    [doSearch, query],
  );

  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
      setSearched(false);
    }
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed left-1/2 top-[15%] w-[560px] -translate-x-1/2 rounded-lg border border-border bg-card shadow-2xl"
          aria-label="Command palette"
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">
            Search the Databricks Capability Graph.
          </Dialog.Description>

          <form onSubmit={handleSubmit} className="relative border-b border-border p-3">
            <Search className="absolute left-5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => handleChange(e.target.value)}
              placeholder="Search capability graph — e.g. 'Delta Live Tables pipeline'"
              className="w-full bg-transparent py-1 pl-7 pr-8 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            {loading && (
              <Loader2 className="absolute right-5 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
            )}
          </form>

          <div className="max-h-[60vh] overflow-y-auto p-2">
            {results.length > 0 ? (
              <ul className="space-y-1">
                {results.map((hit) => (
                  <li
                    key={hit.id}
                    className="cursor-default rounded-md px-3 py-2 transition-colors hover:bg-accent"
                  >
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
                      <div className="min-w-0 flex-1">
                        <p className="line-clamp-2 text-xs leading-relaxed text-foreground/90">
                          {hit.chunk_text.slice(0, 200)}
                          {hit.chunk_text.length > 200 ? "…" : ""}
                        </p>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
                          {hit.meta_skill_id && (
                            <span className="font-mono">{hit.meta_skill_id}</span>
                          )}
                          {hit.score != null && (
                            <span>score {hit.score.toFixed(3)}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : searched && !loading ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                No results found.
              </p>
            ) : !searched ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                Type a question to search across SDK, docs, blog, and labs.
              </p>
            ) : null}
          </div>

          <div className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
            <kbd className="rounded border border-border px-1 py-0.5 font-mono">↵</kbd> search
            <span className="mx-2">·</span>
            <kbd className="rounded border border-border px-1 py-0.5 font-mono">esc</kbd> close
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
