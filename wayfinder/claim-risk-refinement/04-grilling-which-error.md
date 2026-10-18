# Which error do we minimize?
Label: `wayfinder:grilling` · type HITL

## Question

"Error rate" can mean missed disputes at the outreach budget (recall@10%), overall ranking (AUC), or how far predicted percentages sit from what happened (calibration). Which one picks the model?

Recommendation the run proceeds on: pick by recall@10% on 2024 validation (it is what the advocate's daily list lives on); report AUC and the calibration gap as secondary; report 95% bootstrap intervals for each.

## Status: open (HITL), running on the recommendation
