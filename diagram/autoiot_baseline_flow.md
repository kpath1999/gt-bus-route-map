# AutoIOT-Only Baseline Flow

Direct execution baseline with no preprocessing or verification layers.

```mermaid
flowchart TD
    Start([User Query]) --> Agent[Pandas ReAct Agent<br/>ExecutionLayer.execute_single]
    Agent --> Answer([Raw Answer])
    
    Note1[/"⚠️ No concept extraction<br/>⚠️ No schema grounding<br/>⚠️ No guardrail<br/>⚠️ No judge"/]
    
    style Start fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    style Answer fill:#c8e6c9,stroke:#388e3c,stroke-width:2px
    style Agent fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style Note1 fill:#ffebee,stroke:#c62828,stroke-width:2px
```

## Characteristics

- **Simplest baseline**: Raw query sent directly to pandas agent
- **No preprocessing**: Cannot resolve activity names (e.g., "Walking") to codes (e.g., "A")
- **No derived features**: Missing `magnitude` and `activity_name` columns
- **No safety checks**: Executes all queries including out-of-scope requests
- **No verification**: No post-execution judge to validate intent alignment

## Expected Behavior

| Query Type | Behavior |
|------------|----------|
| Direct Answer (Q1-Q4) | ✓ May execute correctly if query maps directly to raw columns |
| Intermediate Reasoning (Q5-Q8) | ⚠️ Likely incorrect due to missing grounding and derived features |
| Out-of-Scope (Q9-Q12) | ⚠️ Executes with low-quality/unsupported answers (no rejection) |

## Code Reference

See [flashfusion/baselines/autoiot_only.py](../flashfusion/baselines/autoiot_only.py#L34-L56)
