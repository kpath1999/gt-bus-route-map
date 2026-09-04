# Flash-Fusion Data Chat

The Vercel chat demo lets people query the bundled bus, ECG, and IMU sensor
streams and inspect how Flash-Fusion turns a natural-language question into a
validated data operation.

## Analysis methods

Use the **Analysis method** selector in the header to compare three execution
paths for the same question:

- **Agent B4** preserves the original multi-stage grounded agent experience.
- **Typed planner** deterministically routes the query to a reduced operator
	vocabulary, requests one structured plan, validates it structurally and
	against the live DataFrame schema, then executes the typed operators.
- **Skeleton cache** first searches the operator-skeleton registry. On a hit,
	a light model binds only the plan parameters to the current schema; the
	normal validation gates run again before execution. A miss or failed cache
	validation transparently falls back to the full typed planner. The Vercel
	demo intentionally uses exact query matching, avoiding an embedding-model
	cold start; semantic matching remains available to benchmark callers.

Typed results include an expandable audit record with the candidate operators,
router exclusions, typed plan, validation outcome, execution certificate,
stage latencies, cache provenance, and fallback trace.

## Local development

Run from the repository root so Python can resolve the sibling `flashfusion`
package used by the typed modes:

```sh
cd chat
pip install -r requirements.txt
vercel dev
```

Set either `OPENROUTER_API_KEY` or `GROQ_API_KEY` in `chat/.env.local`.

## Vercel deployment

Deploy from the repository root, with the Vercel project **Root Directory** set
to the repository root rather than `chat/`. This keeps both the serverless
entrypoint and the `flashfusion/` package in the deployment bundle. Configure
the project to use `chat/vercel.json`, or duplicate its rewrite rules in the
project configuration.

The chat datasets and cache registry must remain in the bundle. ECG and IMU
inputs use Git LFS, so ensure the Vercel build has access to LFS objects before
deploying those two demo domains.
Tool-calling.
Erroring out in CoT.
Syntax issues.
Large model creating reusable functions, MCP.
How do we make the steps more robust so we don't bug out.
