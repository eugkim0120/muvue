# Threat model

This is the real threat model for `muvue serve`'s daemon (v4 §8a: "the
largest defect in v3"). It replaces the placeholder stub this file used
to be (see docs/decisions.md #76 for why a plausible-looking threat
model was deliberately deferred until this analysis was actually done).

## What the daemon is

`muvue serve` binds a local HTTP server that mirrors every CLI verb
(plan §4) and, critically, exposes `POST /nodes/{id}/start?agent=X`:
this launches a configured agent CLI (`[agents.*].command` in
`config.toml`) as a subprocess on the user's machine. v4's own framing
(§1 principle 8): "The daemon is a local code-execution surface. It can
start agents. It is hardened as such, not treated as a convenience UI."
A daemon exposing that endpoint on localhost with no binding
restriction, no origin/CSRF checks, and a session token stored in a
world-readable file (v3's design) is a remote-code-execution surface
reachable from any web page the user has open, or from any other
process running as the same user.

## In scope: what §8a's controls defend against

The threat actors this phase defends against are **remote or
web-page-originated**, not local:

1. **A malicious or compromised web page open in the user's browser**,
   while `muvue serve` is running on the same machine. Without
   controls, such a page could:
   - Issue a same-origin-looking `fetch()`/form submission to
     `http://127.0.0.1:8765/...` and have the browser carry it out —
     classic CSRF against a localhost service.
   - Use **DNS rebinding**: register a domain, get the victim's browser
     to resolve it, then have the DNS answer flip to `127.0.0.1` after
     the browser's same-origin checks have already passed, letting
     page JS talk to the loopback daemon as if it were the attacker's
     own origin.
2. **A cooperating or compromised process on the same machine reading
   a world-readable credential file** (v3's `~/.muvue/session`) to
   acquire every human verb, including `approve`, without ever needing
   network access at all — this is the specific failure v4 calls out
   by name: "v3's `~/.muvue/session` file is removed: an agent can
   read it and acquire every human verb."
3. **A network peer, if the daemon is ever bound beyond loopback** —
   accidentally or otherwise.

## The seven controls, and what each actually buys

1. **Loopback-only bind, `--i-know-this-is-exposed` escape hatch**
   (`cli/main.py::serve`). Defends against (3) directly: the default
   configuration is simply unreachable from another host. The escape
   hatch is not a safety net — it prints a loud warning and does
   nothing else; a user who passes it is fully on their own.
2. **`Host` header validation** (`SecurityMiddleware` in
   `api/app.py`, runs before every route). Defends against DNS
   rebinding in (1): even if a browser's process-level TCP connection
   lands on `127.0.0.1`, the HTTP request it sends carries the
   attacker's chosen `Host:` value, which this check rejects with 403
   before any handler runs.
3. **`Origin` header validation, before auth, no CORS ever emitted.**
   Defends against CSRF in (1) — both the "preflighted" kind (blocked
   anyway by never emitting `Access-Control-Allow-Origin`) and, more
   importantly, **simple-request CSRF**, which never triggers a
   preflight at all. Checking Origin *before* `_require_session` means
   a request that happens to carry a *valid* token but a foreign
   Origin is still rejected — this matters because a token can leak
   (pasted into the wrong place, logged by a proxy) independently of
   whether the request's origin is trustworthy, and the two checks are
   deliberately not allowed to compensate for each other.
4. **POST + `application/json`-only + `Authorization` header (never a
   query string), on *every* mutating endpoint.** The P2/P2b daemon
   only gated the endpoints it called "human verbs"; this phase
   extends the same requirement to agent verbs too, because `start`
   (the RCE endpoint) was one of them and was previously
   unauthenticated by design (see docs/decisions.md #84). JSON-only
   defeats classic `<form>`-submitted CSRF (a plain HTML form cannot
   set `Content-Type: application/json` without JavaScript, and
   JS-driven cross-origin requests are already blocked by control 3).
   Query-string tokens are rejected because they leak into daemon
   access logs, shell history (if a user tests with `curl`), and the
   `Referer` header of any subsequent same-tab navigation.
5. **In-memory-only token, one-time URL fragment, `HttpOnly`
   `SameSite=Strict` cookie exchange.** Defends against (2) directly:
   there is no file to read. The `#fragment` is never sent over HTTP by
   design (browsers strip it before the request line is built), so it
   never touches the daemon's request log even during the one exchange
   call; the cookie that replaces it is `HttpOnly` (page JS, including
   an XSS payload, cannot read it back out) and `SameSite=Strict`
   (never attached to a cross-site request, including image/link-driven
   simple requests a same-origin check alone wouldn't catch).
6. **Token rotates every `serve` restart; 8h idle expiry.** Bounds the
   blast radius of a token that *does* leak (over-the-shoulder, a
   screen share, a copy-pasted log) to, at most, one `serve` session's
   worth of time, and to periods of actual use within it.
7. **`doctor` live probes.** Turns 1-4 from "true today, by
   inspection" into "verified now, by a real request/response" —
   config drift, a future code change that accidentally reopens one of
   these, or a daemon a script started with different flags than
   expected are all caught, not assumed away.

## Explicitly out of scope, and why

- **Same-user process memory/ptrace access.** A process running as the
  same OS user as `muvue serve` can, in general, read that process's
  memory (via `/proc/<pid>/mem`, a debugger, `gcore`, etc.) or attach a
  ptrace-based debugger to it directly. The in-memory session token
  (control 5) is not hidden from this: it is simply not *also* readable
  by every other process the way a world-readable file was. v4 §13
  states this residual risk once, plainly: "A same-user process can
  read the daemon's memory; §8a raises the cost of casual and remote
  attacks, not local ones." Nothing in this phase attempts to solve
  same-user isolation — the plan's own non-goals (§0) rule it out:
  "adversarial agent isolation" is explicitly not a v1 goal.
- **A same-user agent obtaining a TTY.** v4 §4's own honest framing
  (quoted in full because it directly bears on §8a too): "A same-user
  agent with shell access can obtain a TTY (`script`, `pty.spawn`). The
  split [between human and agent verbs] therefore prevents *casual*
  self-approval, not a determined one." `actor_evidence` (threaded
  through every mutating call in the txn-discipline session that
  preceded this one) is the compensating control here, and it is
  **detection, not prevention** — v4 principle 10: "Prevention where
  the user is honest; detection everywhere else." A determined
  same-user adversary that obtains a TTY, or that reads the daemon's
  memory to recover the in-memory token, can still issue authenticated
  requests; those requests are still logged with `actor_evidence`
  (`tty`, `dashboard_token`, `mcp`, `hook`, `subprocess`) and are
  visible in the dashboard's rubber-stamp KPI and event timeline, but
  they are not *blocked*.
- **Prompt injection via MCP.** Contained by the pre-existing verb
  split (human verbs are never exposed over MCP at all — plan §4),
  frozen criteria (Gate 2, plan §5), and the strict-mode airlock (plan
  §5/§9) — not eliminated by anything in this phase. An injected agent
  still cannot call `approve` over MCP; it can still, in principle,
  try to reach the HTTP API directly if it has shell access, which
  folds back into the same-user-process case above.
- **The daemon's own bugs/vulnerabilities in FastAPI/Starlette/
  uvicorn themselves.** Out of scope for a per-repo, single-user local
  tool; tracked only insofar as `docs/decisions.md` records dependency
  versions.

## One honest summary line

Every control in this document raises the cost of a *casual* or
*remote* attack against the daemon to roughly "impossible without
already having code execution as the same OS user." None of them raise
the cost of an attack *by* a process already running as that user —
that boundary is not solvable by this design, is not attempted, and is
stated here once rather than re-litigated per control above.

See also v4 handoff plan §13 ("Residual risks, stated once") for the
plan's own enumeration of what this system does not and cannot solve,
and docs/decisions.md #84-#87 for the specific implementation choices
this phase made and why.
