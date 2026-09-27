import { useState } from "preact/hooks";
import { currentProject } from "../state";
import { Pill } from "../ui/Pill";
import { Icon } from "../ui/Icon";
import { ProjectMenu } from "./ProjectMenu";

export function TopBar({ onSearch }: { onSearch: () => void }) {
  const [menu, setMenu] = useState(false);
  const p = currentProject.value;
  return (
    <header class="topbar">
      <button type="button" class="project-btn" onClick={() => setMenu(true)}>
        <span class="grow project-goal">{p ? p.goal : "no project"}</span>
        {p ? <Pill status={p.phase} /> : null}
      </button>
      <button type="button" class="icon-btn" aria-label="search" onClick={onSearch}><Icon name="search" /></button>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
    </header>
  );
}
