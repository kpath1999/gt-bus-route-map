# Flash-Fusion Baseline Flow

Full pipeline with safety gates, verification, and adaptive retry logic.

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
    Guardrail -->|PROCEED| Agent1[Pandas ReAct Agent<br/>ExecutionLayer.execute_single]
    
    Agent1 --> Judge1{Judge Verdict:<br/>Intent Aligned?}
    
    Judge1 -->|PASS| Final([Final Answer])
    Judge1 -->|FAIL + suggestion| Retry[Reset Agent<br/>+ Append Correction Note]
    
    Retry --> Agent2[Pandas ReAct Agent<br/>Retry with correction]
    Agent2 --> Judge2{Re-judge<br/>Verdict?}
    
    Judge2 --> Final2([Final Answer<br/>after retry])
    
    Note1[/"✓ Full grounding pipeline<br/>✓ Feasibility guardrail<br/>✓ Intent judge + retry<br/>✓ Adaptive correction"/]
    
    style Start fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    style Final fill:#c8e6c9,stroke:#388e3c,stroke-width:3px
    style Final2 fill:#c8e6c9,stroke:#388e3c,stroke-width:3px
    style Reject fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style S1 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S2 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S3 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style Build fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style Guardrail fill:#fff176,stroke:#f57f17,stroke-width:3px
    style Judge1 fill:#81d4fa,stroke:#0277bd,stroke-width:3px
    style Judge2 fill:#81d4fa,stroke:#0277bd,stroke-width:3px
    style Agent1 fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style Agent2 fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style Retry fill:#ffccbc,stroke:#d84315,stroke-width:2px
    style Pipeline fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style Note1 fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
```

## Characteristics

- **Full grounding pipeline**: S1 → S2 → S3 with codebook and derived features
- **Feasibility guardrail**: Rejects queries requiring unavailable schema before execution
- **Intent judge**: Validates that agent's answer aligns with user's original question
- **Adaptive retry**: If judge returns FAIL + suggestion, retries with correction appended
- **One retry only**: Single retry attempt to balance accuracy and latency

## Stage Details

### Stages 1-3: Grounding Pipeline
Same as WellMax-Only (see [wellmax_baseline_flow.md](wellmax_baseline_flow.md))

### Guardrail Check
- **Input**: Grounded query (after S1-S2-S3)
- **Logic**: Verifies query can be answered from available dataset fields
- **Output**: `PROCEED` or `REJECT: <reason>`
- **Rejection examples**: Queries requiring age, gender, GPS location, predictive forecasting

### Judge Verdict
- **Input**: Original query, final code, raw answer
- **Logic**: LLM validates intent alignment between query and answer
- **Output**: `{"verdict": "PASS"|"FAIL", "suggestion": "..."|null}`
- **Retry trigger**: FAIL + non-null suggestion → retry with correction note

### Retry Mechanism
1. Reset agent state
2. Append correction note to grounded query: `"\n\nCorrection note: {suggestion}"`
3. Re-execute agent with modified query
4. Re-judge final answer
5. Return retry result as final answer (no further retries)

## Expected Behavior

| Query Type | Behavior |
|------------|----------|
| Direct Answer (Q1-Q4) | ✓ High accuracy with grounding |
| Intermediate Reasoning (Q5-Q8) | ✓ Best performance with judge retry |
| Out-of-Scope (Q9-Q12) | ✓ Rejected by guardrail with clear reason |

## Code Reference

See [flashfusion/baselines/flash_fusion.py](../flashfusion/baselines/flash_fusion.py#L49-L157)
- Guardrail check: line 102
- Judge verdict: line 136
- Retry logic: lines 143-157
