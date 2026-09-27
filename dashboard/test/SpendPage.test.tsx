import { render, screen } from "@testing-library/preact";
import { SpendPage } from "../src/spend/SpendPage";

afterEach(() => vi.unstubAllGlobals());

test("KPIs and per-agent bars render", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "", headers: new Headers({ "content-type": "application/json" }), text: async () => "",
    json: async () => ({ drift_pct: 0.5, touch_drift: 0.125, rubber_stamp_rate: 0, rubber_stamps: 0, approvals_timed: 4, tokens_per_node: 1234.6, spend_vs_budget: 0.3,
      spend_by_driver: { claude: { spent: 3, limit: 10, unit: "usd", pct: 0.3, warn: false, exhausted: false } } }) })));
  render(<SpendPage />);
  expect(await screen.findByText("12.5%")).toBeInTheDocument();
  expect(screen.getByText("1235")).toBeInTheDocument();
  expect(screen.getByText("claude")).toBeInTheDocument();
  expect(screen.getByText("3 / 10 usd")).toBeInTheDocument();
});
