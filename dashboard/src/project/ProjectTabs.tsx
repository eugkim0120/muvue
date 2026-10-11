import { Segmented } from "../ui/Segmented";
import { Empty } from "../ui/Empty";
import { setProjectTab, type ProjectTab } from "../router";

const TABS: { value: ProjectTab; label: string }[] = [
  { value: "map", label: "Map" },
  { value: "plan", label: "Plan" },
  { value: "agents", label: "Agents" },
  { value: "repo", label: "Repo" },
];

export const PLACEHOLDER_TEXT: Record<"plan" | "repo", string> = {
  plan: "Plan view is not built yet. The Map tab shows the plan.",
  repo: "Repo view is not built yet. The Agents tab lists branches and worktrees.",
};

export function ProjectTabs({ tab }: { tab: ProjectTab }) {
  return (
    <nav class="project-tabs" aria-label="project views">
      <Segmented options={TABS} value={tab} onChange={(v) => setProjectTab(v as ProjectTab)} />
    </nav>
  );
}

export function TabPlaceholder({ text }: { text: string }) {
  return <div class="page"><Empty text={text} /></div>;
}
