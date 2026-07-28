"""
User query
   |
(i) Guardrail, fixed model
   |-- REJECT --> return OOS response
   |
(ii-a) Complexity classifier, heuristic or very cheap LLM
   |-- direct | multi_step | predictive
   |
(ii-b) Code-generation model selection
   |-- direct     --> small, fast code/instruction model
   |-- multi_step --> medium code/reasoning model
   |-- predictive --> large code/reasoning model
   |
Safe execution and retry
   |
(iii) Answer synthesis, fixed light model



| Tier       | Operational definition                                                                                                     | Typical IoT query                                                   | Codegen requirement                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------- |
| direct     | One filter/group/aggregate/rank operation; no derived feature requiring multiple stages                                    | “Maximum x acceleration for user 15”                                | Small, fast instruction or code model                   |
| multi_step | Derived feature, multiple filters/aggregations, comparison, time/duration logic, correlation, ranking after transformation | “Compare acceleration magnitude for dynamic vs. resting activities” | Medium coding/reasoning model                           |
| predictive | Explicit train/holdout/split/model/forecast/classify/predict request                                                       | “Train a classifier on the first 80% and predict the next activity” | Large coding/reasoning model; execution-aware prompting |

"""