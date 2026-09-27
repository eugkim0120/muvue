import { route, navigate } from "../router";
import { Segmented } from "../ui/Segmented";
import { Timeline } from "./Timeline";
import { Revisions } from "./Revisions";

export function ActivityPage() {
  const seg = route.value.params[0] === "revisions" ? "revisions" : "timeline";
  return (
    <div class="page stack">
      <div class="row between">
        <h1 class="page-title">Activity</h1>
        <Segmented options={[{ value: "timeline", label: "Timeline" }, { value: "revisions", label: "Revisions" }]} value={seg} onChange={(v) => navigate(v === "revisions" ? "#/activity/revisions" : "#/activity")} />
      </div>
      {seg === "timeline" ? <Timeline /> : <Revisions />}
    </div>
  );
}
