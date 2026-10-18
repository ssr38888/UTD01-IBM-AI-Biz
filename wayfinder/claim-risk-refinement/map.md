# Map: Claim risk model refinement
Label: `wayfinder:map` · tracker: local markdown (no issue tracker configured; tickets are files in this folder)

## Destination

A dispute-risk model whose error on the held-out 2025 year is as low as the public data allows, with honest error bars, pushed by 13:30 UTC on 18 Oct 2026 so the dashboard and 3-minute video can use it. Real-data results reported apart from simulated-feature results.

## Notes

- Domain: NY Workers' Comp, label = recorded dispute 1–90 days after assembly; train 2023, validation 2024, test 2025 (read once per final model).
- Skills: `supermatt:research` for research tickets; `grilling` + `domain-modeling` for HITL tickets.
- Standing preference (Jack, 18 Oct): accept the agent's recommendations automatically. Each HITL ticket below states the recommendation the run proceeds on; Jack overrides by answering the ticket.
- Hard stop: deadline 14:00 UTC; checkpoint and push the best model as it improves.

## Decisions so far

<!-- one line per closed ticket -->

- [Are the extra public fields known when a claim is assembled?](01-research-day0-fields.md): snapshot data; drop accident/occupational-disease rulings, use C-2/C-3 only if dated on or before assembly
- [Pull the extra day-0 fields for 2023–2025](02-task-fetch-public-fields.md): 810,613 claims joined by claim id
- [Should gender, age and ZIP feed the score, or only the fairness audit?](03-grilling-protected-attributes.md): fairness audit only; costs 0.1 point
- [Which error do we minimize?](04-grilling-which-error.md): recall@10% on validation picks; AUC and calibration reported
- [Tune, blend and refit with the decided features](05-task-tune-and-blend.md): 2025 recall@10% 58.4% (95% CI 57.8–59.3), up from 51.0%; MLP blend dropped

## Not yet specified

- Whether a 180-day label (the original plan) beats 90 days once outcomes for late 2025 mature.
- A second "time to dispute" head (survival model) for the advocate's lead-time window.
- Whether the fall-video model's real outputs can replace the simulated video features once FallWatch has real claims linked.

## Out of scope

- Hosting or serving the model anywhere beyond this laptop and the LinuxONE VM (Jack: "keep the model local").
- Any outcome-time field as a predictor (attorney, hearings, injury type, wage, status): leakage, ruled out by the data handoff.
