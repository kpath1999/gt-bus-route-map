# WellMax-Only Baseline Flow

Three-stage query grounding pipeline without safety gates or verification.

```mermaid
flowchart TD
    Start([User Query]) --> S1
    
    subgraph Pipeline["3-Stage Grounding Pipeline"]
        S1[Stage 1:<br/>Concept Extraction] --> S2[Stage 2:<br/>Schema Grounding<br/>+ codebook if adapter]
        S2 --> S3[Stage 3:<br/>Sub-query Generation]
    end
    
    S3 --> Build[/"Build Grounded Query<br/>(concepts + mappings + sub-tasks)"/]
    Build --> Agent[Pandas ReAct Agent<br/>ExecutionLayer.execute_single]
    Agent --> Answer([Raw Answer])
    
    Note1[/"✓ Concept-to-column mapping<br/>✓ Activity codebook injection<br/>✓ Derived features (magnitude)<br/>⚠️ No guardrail<br/>⚠️ No judge"/]
    
    style Start fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    style Answer fill:#c8e6c9,stroke:#388e3c,stroke-width:2px
    style S1 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S2 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style S3 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style Build fill:#fff9c4,stroke:#f9a825,stroke-width:2px
    style Agent fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style Pipeline fill:#fce4ec,stroke:#c2185b,stroke-width:2px
    style Note1 fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
```

## Characteristics

- **Grounded execution**: Maps indirect concepts (e.g., "sedentary") to exact columns before execution
- **Activity codebook**: S2 injects mapping (A=Walking, B=Jogging, C=Stairs, etc.) when adapter is provided
- **Derived features**: `magnitude` and `activity_name` are available when adapter-derived features are applied by the runner
- **Query decomposition**: S3 breaks complex queries into sub-tasks for agent
- **No safety gates**: Missing guardrail (executes out-of-scope) and judge (no retry)

## Stage Details

### Stage 1: Concept Extraction
Classifies query concepts as DATA (column-mappable) or REASONING (proxy-required)

### Stage 2: Schema Grounding
- Maps DATA concepts → actual column names
- Maps REASONING concepts → column + operation proxies
- Injects activity codebook for label resolution

### Stage 3: Sub-query Generation
Decomposes abstract query into 2-4 concrete, column-grounded sub-questions

## Expected Behavior

| Query Type | Behavior |
|------------|----------|
| Direct Answer (Q1-Q4) | ✓ Typically correct due to grounding |
| Intermediate Reasoning (Q5-Q8) | ✓ Stronger execution vs AutoIOT (has derived features) |
| Out-of-Scope (Q9-Q12) | ⚠️ Still executes with weak/unsupported results (no guardrail) |

## Code Reference

See [flashfusion/baselines/wellmax_only.py](../flashfusion/baselines/wellmax_only.py#L47-L95)
