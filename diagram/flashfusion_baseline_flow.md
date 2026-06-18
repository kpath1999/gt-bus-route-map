# Flash-Fusion Baseline Flow

Full pipeline with safety gates and pre-execution plan refinement.

```mermaid
flowchart TD
    Start([User Query]) --> S1
    
    subgraph Pipeline["3-Stage Grounding Pipeline"]
        S1[Stage 1:<br/>Concept Extraction] --> S2[Stage 2:<br/>Schema Grounding<br/>+ Activity Codebook]
        S2 --> S3[Stage 3:<br/>Sub-query Generation]
    end
    
    S3 --> Build[/"Build Grounded Query<br/>(concepts + mappings + sub-tasks)"/]
    Build --> Guardrail{Guardrail Check:<br/>Feasible?}
    
    Guardrail -->|REJECT| Reject([Rejection Response<br/>with reason])
    Guardrail -->|PROCEED| PlanJudge{Plan Judge:<br/>S3 answers intent?}
    
    PlanJudge -->|PASS| Agent[Pandas ReAct Agent<br/>ExecutionLayer.execute_single]
    PlanJudge -->|FAIL + suggestion| Refine[Regenerate Stage-3 once<br/>with correction note]
    Refine --> PlanJudgeRetry{Plan Judge Retry:<br/>Improved?}
    PlanJudgeRetry --> Agent
    
    Agent --> Final([Final Answer])
    
    Note1[/"✓ Full grounding pipeline<br/>✓ Feasibility guardrail<br/>✓ Pre-agent plan judge<br/>✓ One-shot S3 refinement"/]
    
    style Start fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    style Final fill:#c8e6c9,stroke:#388e3c,stroke-width:3px
    style Reject fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style S1 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S2 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S3 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style Build fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style Guardrail fill:#fff176,stroke:#f57f17,stroke-width:3px
    style PlanJudge fill:#81d4fa,stroke:#0277bd,stroke-width:3px
    style PlanJudgeRetry fill:#4fc3f7,stroke:#01579b,stroke-width:3px
    style Agent fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style Refine fill:#ffccbc,stroke:#d84315,stroke-width:2px
    style Pipeline fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style Note1 fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
```

## Characteristics

- **Full grounding pipeline**: S1 → S2 → S3 with codebook and derived features
- **Feasibility guardrail**: Rejects queries requiring unavailable schema before execution
- **Plan judge**: Validates Stage-3 decomposition before any code execution
- **Adaptive plan refinement**: If plan judge returns FAIL + suggestion, regenerates S3 once
- **Single-pass execution**: Agent executes once after plan acceptance

## Stage Details

### Stages 1-3: Grounding Pipeline
Same as WellMax-Only (see [wellmax_baseline_flow.md](wellmax_baseline_flow.md))

### Guardrail Check
- **Input**: Grounded query (after S1-S2-S3)
- **Logic**: Verifies query can be answered from available dataset fields
- **Output**: `PROCEED` or `REJECT: <reason>`
- **Rejection examples**: Queries requiring age, gender, GPS location, predictive forecasting

### Plan Judge
- **Input**: Original query, S2 grounding, S3 sub-queries, synthesis hint
- **Logic**: LLM validates whether the decomposition is complete, ordered, and executable
- **Output**: `{"verdict": "PASS"|"FAIL", "suggestion": "..."|null}`
- **Refinement trigger**: FAIL + non-null suggestion → regenerate Stage-3 once

### Plan Refinement Mechanism
1. Append correction note to Stage-3 query context
2. Regenerate sub-queries and synthesis hint once
3. Rebuild grounded query and re-run plan judge
4. Execute pandas agent after plan gate is satisfied

## Expected Behavior

| Query Type | Behavior |
|------------|----------|
| Direct Answer (Q1-Q4) | ✓ High accuracy with grounding |
| Intermediate Reasoning (Q5-Q8) | ✓ Improved decomposition via plan judge + one refinement |
| Out-of-Scope (Q9-Q12) | ✓ Rejected by guardrail with clear reason |

## Code Reference

See [flashfusion/baselines/flash_fusion.py](../flashfusion/baselines/flash_fusion.py)
- Guardrail check: `guardrail`
- Plan judge gate: `judge_plan`
- One-shot plan refinement: `S3_refine` + `judge_plan_retry`
