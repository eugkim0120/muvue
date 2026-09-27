import { useState } from "preact/hooks";
import { route, navigate } from "../router";
import { currentProject, inboxCount, authed, protocolVersion } from "../state";
import { Icon } from "../ui/Icon";
import { Pill } from "../ui/Pill";
import { ProjectMenu } from "./ProjectMenu";

export const NAV = [
  { page: "plan", label: "Plan", icon: "plan" },
  { page: "inbox", label: "Inbox", icon: "inbox" },
  { page: "activity", label: "Activity", icon: "activity" },
  { page: "spend", label: "Spend", icon: "spend" },
] as const;

export function isActive(page: string, current: string): boolean {
  return current === page || (page === "plan" && current === "spec");
}

export function Sidebar() {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <aside class="sidebar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow">
          <span class="project-goal">{p ? p.goal : "no project"}</span>
          {p ? <Pill status={p.phase} /> : null}
        </span>
        <Icon name="more" />
      </button>
      <nav>
        {NAV.map((n) => (
          <button type="button" class={"nav-item" + (isActive(n.page, route.value.page) ? " on" : "")} onClick={() => navigate("#/" + n.page)}>
            <Icon name={n.icon} />
            <span class="grow">{n.label}</span>
            {n.page === "inbox" && inboxCount.value > 0 ? <span class="badge">{inboxCount.value}</span> : null}
          </button>
        ))}
      </nav>
      <div class="sidebar-foot caption">
        <div>{authed.value ? "signed in" : "read-only"}</div>
        {protocolVersion.value !== null ? <div>protocol v{protocolVersion.value}</div> : null}
      </div>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </aside>
  );
}
