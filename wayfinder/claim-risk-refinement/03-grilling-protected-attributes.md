# Should gender, age and ZIP feed the score, or only the fairness audit?
Label: `wayfinder:grilling` · type HITL · blocked by: 01, 02

## Question

Gender and age are protected attributes and ZIP is a proxy for race and income. Do they go into the model that ranks workers for outreach, or are they kept out of the score and used only to check fairness?

Recommendation the run proceeds on (Jack overrides by answering): keep gender and age OUT of the score, keep ZIP area out too; report the model with and without them so the cost is visible; use all three for the fairness tables. Rationale: NYS-P24-001 bias plan and the pitch's governance panel are stronger if the score never sees protected traits.

## Status: open (HITL), running on the recommendation

Evidence for Jack: with gender, age and ZIP added, 2025 recall@10% is 58.5% (95% CI 57.7–59.2) vs 58.4% (57.8–59.3) without. Excluding them costs nothing measurable.
