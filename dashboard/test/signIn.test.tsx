import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AuthStrip } from "../src/shell/AuthStrip";
import { SignInSheet } from "../src/shell/SignInSheet";
import { authed, signedOutReason, signInOpen } from "../src/state";
import { setToken } from "../src/api/client";

function stubAuth(check: { status: number; auth?: string }) {
  const fn = vi.fn(async (url: string) => {
    const r = url === "/auth/check" ? check : { status: 200 };
    return { ok: r.status < 400, status: r.status, statusText: "s", headers: new Headers({ "content-type": "application/json", ...(r.auth ? { "x-muvue-auth": r.auth } : {}) }), json: async () => ({ detail: "d" }), text: async () => "" };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => { vi.unstubAllGlobals(); setToken(""); authed.value = false; signedOutReason.value = "read_only"; signInOpen.value = false; });

test("the strip says why the page is read-only, and hides when signed in", () => {
  const { rerender } = render(<AuthStrip />);
  expect(screen.getByText("Read-only view.")).toBeTruthy();
  signedOutReason.value = "stale";
  rerender(<AuthStrip />);
  expect(screen.getByText("Signed out: muvue serve restarted since you signed in.")).toBeTruthy();
  signedOutReason.value = "expired";
  rerender(<AuthStrip />);
  expect(screen.getByText("Signed out after 8 hours without activity.")).toBeTruthy();
  expect(screen.getByText("Sign in again")).toBeTruthy();
  authed.value = true;
  rerender(<AuthStrip />);
  expect(document.querySelector("[data-auth-strip]")).toBeNull();
});

test("the strip's button opens the sign-in sheet", () => {
  render(<AuthStrip />);
  fireEvent.click(screen.getByText("Sign in"));
  expect(signInOpen.value).toBe(true);
});

test("a refused token shows why, in the sheet", async () => {
  stubAuth({ status: 403, auth: "invalid" });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "wrong" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(document.querySelector("[data-sign-in-error]")?.textContent).toBe(
    "That token was not accepted. Tokens change every time muvue serve restarts — run muvue link for the current one.",
  ));
  expect(authed.value).toBe(false);
});

test("an expired token says so and marks the page expired", async () => {
  stubAuth({ status: 401, auth: "expired" });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "old" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(document.querySelector("[data-sign-in-error]")?.textContent).toContain("expired after 8 hours"));
  expect(signedOutReason.value).toBe("expired");
});

test("a good token signs in and closes the sheet", async () => {
  signInOpen.value = true;
  stubAuth({ status: 200 });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "good" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(authed.value).toBe(true));
  expect(signInOpen.value).toBe(false);
});

test("Use token is disabled until something is typed", () => {
  render(<SignInSheet />);
  expect((screen.getByText("Use token").closest("button") as HTMLButtonElement).disabled).toBe(true);
});
