import { useState } from "preact/hooks";
import { post } from "../api/client";
import { routes } from "../api/routes";
import { loadProjects, projectId, toastError } from "../state";
import { openProject } from "../router";
import { Button } from "../ui/Button";

export function NewProject() {
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(e: Event) {
    e.preventDefault();
    if (!goal.trim()) return;
    setBusy(true);
    try {
      const r = await post<{ project: { id: number } }>(routes.projects(), { goal });
      await loadProjects();
      projectId.value = r.project.id;
      openProject(r.project.id);
    } catch (e) { toastError(e); } finally { setBusy(false); }
  }
  return (
    <div class="page">
      <form class="card stack" style={{ maxWidth: 480, margin: "10dvh auto" }} onSubmit={create}>
        <h1 class="page-title">Start a project</h1>
        <input placeholder="goal" value={goal} onInput={(e) => setGoal((e.target as HTMLInputElement).value)} />
        <div class="actions"><Button type="submit" variant="filled" disabled={busy}>Create</Button></div>
      </form>
    </div>
  );
}
