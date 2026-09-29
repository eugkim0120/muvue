import { render, waitFor, act } from "@testing-library/preact";
import { useActivity } from "../src/project/activity";
import * as client from "../src/api/client";
import { ApiError } from "../src/api/client";
import { authed, toasts } from "../src/state";

function Probe() {
  useActivity(1);
  return null;
}

afterEach(() => { vi.restoreAllMocks(); toasts.value = []; authed.value = false; });

test("a signed-out guest does not fetch the guarded activity route and sees no error toast", async () => {
  authed.value = false;
  const spy = vi.spyOn(client, "api").mockRejectedValue(new ApiError("missing session token", 403, "missing"));
  render(<Probe />);
  await act(async () => { await Promise.resolve(); });
  expect(spy).not.toHaveBeenCalled();
  expect(toasts.value).toHaveLength(0);
});

test("an auth failure from the activity route is not toasted", async () => {
  authed.value = true;
  const spy = vi.spyOn(client, "api").mockRejectedValue(new ApiError("missing session token", 403, "missing"));
  render(<Probe />);
  await waitFor(() => expect(spy).toHaveBeenCalled());
  await act(async () => { await Promise.resolve(); });
  expect(toasts.value).toHaveLength(0);
});

test("a non-auth failure is still toasted", async () => {
  authed.value = true;
  vi.spyOn(client, "api").mockRejectedValue(new ApiError("boom", 500, null));
  render(<Probe />);
  await waitFor(() => expect(toasts.value.map((t) => t.text)).toContain("boom"));
});

test("signing in triggers the activity fetch", async () => {
  authed.value = false;
  const spy = vi.spyOn(client, "api").mockResolvedValue({ active: [], breakdowns: [], working: [] });
  render(<Probe />);
  await act(async () => { await Promise.resolve(); });
  expect(spy).not.toHaveBeenCalled();
  await act(async () => { authed.value = true; });
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
});
