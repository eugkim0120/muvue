# Threat model

**Status: placeholder.** This file exists because v4 working rule 6
lists `docs/threat-model.md` as a new required doc, and this session
(the v4 §1.2/§3 foundational transaction-discipline-and-schema slice)
is not the one scoped to write its real content.

The actual threat model belongs to v4 §8a ("Daemon security (new; the
largest defect in v3)") — binding, origin/Host validation, CSRF, token
lifecycle and storage, and the residual same-user-process risks — which
is explicit, separate follow-up work. This session's task boundary says
plainly: do not touch daemon security, budgets, the hook fast path,
parallel-mode restriction, branch coherence, structure-ref commits, or
trailer-enforcement relocation.

Writing a plausible-looking threat model now, without having done §8a's
analysis, would be worse than an honest stub — see docs/decisions.md
#76.

When the §8a session lands, this file should cover at minimum:

- The daemon's attack surface (`POST /nodes/{id}/start?agent=X` as a
  local code-execution primitive — v4 §8a's own framing).
- Binding (`127.0.0.1` only, `--i-know-this-is-exposed` escape hatch).
- `Host`/`Origin` validation and why (DNS rebinding, CSRF).
- Session token lifecycle: minted in memory, delivered via a one-time
  URL fragment, never written to disk (v3's `~/.muvue/session` file
  removed).
- What is explicitly *not* solved here: same-user process memory
  reads/ptrace, prompt injection via MCP (contained, not eliminated, by
  the verb split / frozen criteria / airlock), and the honest
  human-vs-agent-verb boundary (`actor_evidence` is detection, not
  prevention — see v4 §4's own framing, and this session's threading of
  `events.actor_evidence` as the first, purely observational, piece of
  that compensating control).

See also v4 handoff plan §13 ("Residual risks, stated once") for the
plan's own enumeration of what this system does not and cannot solve.
