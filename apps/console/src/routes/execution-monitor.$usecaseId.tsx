import { createFileRoute } from "@tanstack/react-router";

import { UsecaseExecutionMonitorPage } from "./usecases.$usecaseId.executions";

export const Route = createFileRoute("/execution-monitor/$usecaseId")({
  component: ExecutionMonitorRoutePage,
});

function ExecutionMonitorRoutePage() {
  const { usecaseId } = Route.useParams();
  return <UsecaseExecutionMonitorPage usecaseId={usecaseId} />;
}
