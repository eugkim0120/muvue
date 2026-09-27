import { route, navigate } from "../router";
import { inboxCount } from "../state";
import { Icon } from "../ui/Icon";
import { NAV, isActive } from "./Sidebar";

export function TabBar() {
  return (
    <nav class="tabbar">
      {NAV.map((n) => (
        <button type="button" class={"tab" + (isActive(n.page, route.value.page) ? " on" : "")} onClick={() => navigate("#/" + n.page)} aria-label={n.label}>
          <span class="tab-icon"><Icon name={n.icon} />{n.page === "inbox" && inboxCount.value > 0 ? <span class="badge">{inboxCount.value}</span> : null}</span>
          <span class="tab-label">{n.label}</span>
        </button>
      ))}
    </nav>
  );
}
