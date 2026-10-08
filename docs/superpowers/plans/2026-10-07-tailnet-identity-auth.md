# Permanent dashboard access via Tailscale identity, then a better voxscore DAG view

## Context

The one-time dashboard link (`#n=` nonce, `daemon.py:217 consume_nonce`) dies on first
use, so opening it in a second browser, on a phone, or through a chat preview burns it.
The user chose **Tailscale identity** as the durable credential: `muvue serve` already
binds a Tailscale IP (100.83.140.92) behind `--i-know-this-is-exposed`, and
`tailscale whois --json <peer-ip>` works from this host (LocalAPI socket
`/var/run/tailscale/tailscaled.sock` is world-rw; verified for 100.98.213.110).
The user also wants the whole "look at the voxscore DAG" process improved (getting there,
live state, understanding the plan, acting). The research fork's UI findings were not
fully verified (its claim that the diagram starts ~690px down on phone conflicts with my
own earlier ui-check measurement of 465px first-run), so the UI work below is scoped as
"re-measure first".

Split into three independent specs, each its own plan cycle. **A is the only one planned
in detail here.** B and C are outlined and need their own brainstorm/approval.

## Spec A: tailnet identity auth (do first)

Design (recommended): a request whose TCP peer is a tailnet address whose whois login is on
an allowlist is treated as an authenticated session. No nonce, no cookie, no token
paste. The plain URL works as a permanent bookmark on every device on the tailnet.

Rules (security-sensitive; each needs a test):
1. Identity comes only from the socket peer (`request.client.host`). Never from
   `X-Forwarded-For` or any header. `serve` binds the Tailscale IP directly, so there is no proxy.
2. Allowlist: new `[daemon] tailnet_logins = []` in `config.toml` (`config.py:46-52`,
   default template at `config.py:194`). Empty list means the feature is off, so existing
   behaviour is unchanged unless opted in. A non-empty list matches `UserProfile.LoginName`
   from whois (the user's is `eugkim0120@gmail.com`; confirm the exact string from whois
   output at implementation time).
3. Deny tagged nodes and shared-in nodes (whois `Node.Tags` non-empty, or the node's `User`
   differs from the allowlisted profile). A tailnet member who is not on the list gets the
   existing signed-out read-only view (Task 1 behaviour), not a 403 wall.
4. whois failure (socket missing, CLI error, timeout) means "not tailnet-authed", logged
   once with the reason, falling back to the token/nonce flow. No silent success, no
   fallback to trusting the IP.
5. Cache per peer IP for 60 s (bounded dict, no unbounded growth) so polling does not
   fork whois per request. Use the LocalAPI over the Unix socket via stdlib
   (`http.client` over `AF_UNIX`), not the `tailscale` CLI subprocess, to avoid a fork
   per cache miss. No new runtime dependency.
6. Mutating verbs keep the existing origin check (`_origin_ok`, `app.py:138`) and
   JSON-only content-type rule. Whois replaces sign-in, not the per-request CSRF checks.
   Because the cookie is gone, `SameSite` protection is gone too, so the Origin/Host
   check becomes the only CSRF defence for tailnet-authed requests. Add a test that a
   cross-origin POST from an allowlisted IP is refused.
7. Idle expiry (#173) does not apply to tailnet-authed requests: identity is re-verified
   every cache window, which is stronger than an idle clock. The API token and nonce flow,
   including its 8h idle expiry, are unchanged for loopback and non-allowlisted clients.
8. The VS Code iframe keeps its header-token path (loopback, not tailnet).

Code touch points:
- `src/muvue/core/tailnet.py` (new, small): `whois(ip) -> TailnetPeer | None`, TTL cache,
  LocalAPI client. Injectable transport for tests.
- `src/muvue/api/app.py`: extend `_require_session` (line 211) with a final
  "tailnet peer is allowlisted" branch; `/auth/check` already reports ok, which is what the
  dashboard uses to decide it is signed in, so the page needs no nonce to come up
  authed. Add `GET /auth/whoami` returning the matched login or null, for the UI strip.
- `src/muvue/core/config.py`: `tailnet_logins`.
- `src/muvue/cli/main.py` `link` (line 434): print the plain URL, and say which auth
  applies ("tailnet identity: eugkim0120@gmail.com" or "needs one-time link").
- `src/muvue/core/doctor.py`: report whether the LocalAPI is reachable and whether a
  whois of this host resolves; warn if the allowlist is non-empty but whois fails.
- Docs: new decision #176 (supersedes the "token only" assumption in threat-model
  control 5 for allowlisted tailnet peers; #173 unchanged for everyone else) and a
  threat-model control entry stating the new trust assumption: any process on an
  allowlisted device, and anyone with a Tailscale-authenticated session on it, can
  act. Update CHANGELOG.
- Dashboard (`dashboard/src`): the sign-in strip reads "Signed in via Tailscale
  (<login>)" and hides token paste when whoami is non-null. Rebuild and recopy the
  bundle to `src/muvue/api/static/index.html` (test_dashboard_static checks it).

Tests (red first): fake whois transport; allowlisted IP authed without cookie; other tailnet
login read-only; tagged node denied; `X-Forwarded-For` ignored; whois failure falls back
and logs; empty allowlist leaves behaviour unchanged; cross-origin POST refused;
cache expiry; background-header polling does not matter. Plus a live check on this host
against 100.83.140.92 from the second tailnet device (the Windows laptop) if reachable,
otherwise state it was not run.

Residual risk to state in the decision: if the tailnet ACL ever lets a device you do not
control reach port 8766 under your own login, it gets full control. Mitigation is the
allowlist plus ACLs, not code.

## Spec B (outline, own brainstorm): phone-first layout
Re-measure with ui-check first, then likely: diagram above the status stack on phone,
one-line status summary, status word on the node's top row, zoom bar not covering nodes,
two-line title wrap, a single sign-in prompt (partly done in 8ad526c).

## Spec C (outline, own brainstorm): process
`muvue serve` stays running reliably (systemd user unit), `muvue link` prints the stable
URL, daemon-stale-after-merge detection (compare process start to installed version, in
`doctor` and as a dashboard banner), one command to restart.

## Verification (Spec A)
`uv run pytest -q` (baseline 977), `cd dashboard && npx vitest run && npx tsc --noEmit &&
npm run build`, `ui-check` 54/54, then: restart the voxscore daemon (needs the user's
go-ahead), open the bare URL from a second device with no link, confirm it is signed in,
confirm a non-allowlisted peer sees read-only, confirm `doctor` output.
