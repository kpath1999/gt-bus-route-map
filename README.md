# Flash-Fusion: - Enabling Expressive, Low-Latency Queries on IoT Sensor Streams with LLMs

**[Dashboard](https://sting-sense-map.vercel.app/)** | **[GitHub Repository](https://github.com/kpath1999/sting-sense-map)**

---

## General flow with prompts

The system answers conversational natural language queries over tabular IoT data using a 4-stage pipeline designed to prevent hallucinations and ensure schema-grounded pandas code generation. The pipeline is implemented via LangChain and the Groq (Llama 3) API. Check [the evaluation script](src/scripts/eval.py) for how we got this done.

### 1. Schema-Aware Query Rewriter
Before touching the dataframe, an LLM rewrites the user's conversational query into a strict, column-grounded version. It is fed pre-computed metadata (data types, min/max bounds, sample values) for every column.
* **Goal:** Map vague concepts (e.g., "jerkiest") to exact columns (e.g., `accel_variance`).
* **Rule:** If a data concept cannot be mapped, it is sent to an `UNMAPPABLE` list. Mathematical operations (standard deviation, mean, etc.) are protected and passed through.

### 2. Guardrail Gatekeeper
If the rewriter detects `UNMAPPABLE` concepts, the query is rejected immediately. Otherwise, the rewritten query is passed to a strict gatekeeper prompt.
* **Goal:** Approve (`PROCEED`) or reject (`REJECT: <reason>`) queries based *only* on the available schema.
* **Rule:** Rejects queries requiring external internet data, missing base columns (e.g. asking for temperature when only acceleration exists), or predictive forecasting.

### 3. Pandas DataFrame Agent
Once approved, the rewritten query is handed to a LangChain zero-shot ReAct Pandas DataFrame Agent.
* **Prompt Engineering:** The agent is given a strict system prompt containing the exact dataframe columns and total row counts.
* **Tool Usage:** It is instructed to use a single tool (`python_repl_ast`) to execute valid pandas code against the dataframe.

### 4. Natural Language Contextualization
The Pandas agent returns a raw statistical or geographical answer (e.g., `59` or `latitude: 33.7, longitude: -84.3`).
* **Goal:** Transform the raw data into a human-readable response.
* **Outcome:** The final LLM synthesizes the user's original query and the agent's raw output into a concise natural language sentence, hiding the underlying code and statistics.

## Sample prompts

See [`src/scripts/queries.py`](src/scripts/queries.py) list.
