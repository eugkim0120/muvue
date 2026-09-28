import { useState } from "preact/hooks";
import { projects, projectId, currentProject, authed, protocolVersion, refresh } from "../state";
import { openProject } from "../router";
import { Icon } from "../ui/Icon";
import { Pill } from "../ui/Pill";
import { ProjectMenu } from "./ProjectMenu";

export function Sidebar() {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <aside class="sidebar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow"><span class="project-goal">{p ? p.goal : "no project"}</span>{p ? <Pill status={p.phase} /> : null}</span>
        <Icon name="more" />
      </button>
      <nav>
        {projects.value.map((pr) => (
          <button type="button" class={"nav-item" + (pr.id === projectId.value ? " on" : "")} onClick={() => { projectId.value = pr.id; refresh(); openProject(pr.id); }}>
            <span class="dot" style={{ background: pr.phase === "closed" ? "var(--st-done)" : "var(--st-ready)" }} />
            <span class="grow">{pr.goal}</span>
          </button>
        ))}
        <button type="button" class="nav-item" onClick={() => openProject(0)}><Icon name="plan" /><span class="grow">+ New project</span></button>
      </nav>
      <div class="sidebar-foot caption">
        <div>{authed.value ? "signed in" : "read-only"}</div>
        {protocolVersion.value !== null ? <div>protocol v{protocolVersion.value}</div> : null}
      </div>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </aside>
  );
}
