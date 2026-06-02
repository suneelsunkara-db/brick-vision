/*
 * TypeScript mirrors of the capability_graph + Knowledge UI Python
 * dataclasses. The build pipeline + visual builder + observability
 * shapes were retired in v0.7.7; only the Knowledge UI surface ships
 * in v0.6 (per docs/23-databricks-capability-graph.md).
 */

export type ReasonCode =
  | "CAPABILITY_GRAPH_SOURCE_FETCH_FAILED"
  | "CAPABILITY_GRAPH_SMOKE_REGRESSION"
  | "CAPABILITY_GRAPH_PROMOTION_HITL_REQUIRED"
  | "INDEXER_SP_PRIVILEGE_INSUFFICIENT"
  | "VECTOR_SEARCH_INDEX_SYNC_LAG_EXCEEDED"
  | "BUDGET_NAMESPACE_OVER_BUDGET"
  | string; // open enum
