import { projects, projectId, authed, signedOutReason, protocolVersion, refresh } from "../state";
import { openProject } from "../router";
import { Icon } from "../ui/Icon";

export function Sidebar() {
  return (
    <aside class="sidebar">
      <div class="sidebar-head caption">Projects</div>
      <nav>
        {projects.value.map((pr) => (
          <button type="button" class={"nav-item" + (pr.id === projectId.value ? " on" : "")} onClick={() => { projectId.value = pr.id; refresh(); openProject(pr.id); }}>
            <span class="dot" style={{ background: pr.phase === "closed" ? "var(--st-done)" : "var(--st-ready)" }} />
            <span class="grow" title={pr.goal}>{pr.goal}</span>
          </button>
        ))}
        <button type="button" class="nav-item" onClick={() => openProject(0)}><Icon name="plan" /><span class="grow">+ New project</span></button>
      </nav>
      <div class="sidebar-foot caption">
        <div>{authed.value ? "signed in" : signedOutReason.value === "expired" ? "session expired" : "read-only"}</div>
        {protocolVersion.value !== null ? <div>protocol v{protocolVersion.value}</div> : null}
      </div>
    </aside>
  );
}
