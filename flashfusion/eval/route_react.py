"""
eval/trace_react_components.py - Granular view of ReAct-Only

Focus: codegen, execution, retries, answer
Step-level failure modes too!
"""


def markdown_report(q: QueryEval) -> str:
    lines = []
    lines.append(f'# ReAct component trace (with the grounding and out-of-scope check)')
    lines.append("")
    lines.append(f'- Query: q.query')
    lines.append(f"- Total latency: {q.total_latency_s:.2f}s")
    lines.append(f"- Codegen total: {q.codegen_total_s:.2f}s")
    lines.append(f"- Execution total: {q.exec_total_s:.2f}s")
    lines.append(f"- Answer total: {q.answer_total_s:.2f}s")
    lines.append(f"- Rejected at the start: {q.rejected}")

"""
class ThinkingCaptureHandler from executor.py would be helpful here.
recording the latency with each call: code-gen, execution, answer
_build_prefix in ExecutionLayer has been shortened; only have it focus on the out-of-scope part.
predictive tasks can be handled in the next prompt stage; routing layer would be able to grade the query based on its complexity. i don't want to hardcode queries as direct, reasoning, predictive -- the routing layer would have to decide by itself.
"""

"""
- we need to confirm whether the occasional slowness of codegen is token-driven or queueing driven.
- log WCT immediately before and after each LLM call, and run the same query 5-10 times.
If we are running the query several times, we should be tracking percentile latency, not average. track p50 and p95 as well.
-- the two metrics that would matter: time-to-first-token (TTFT), the delay between the initial request and the first output token, and time per output token (TPOT), also called inter-token latency -- how fast tokens stream once generation starts.
-- if latency for the identical query varies by more than ~30% run-to-run, that would be provider-side jitter, not the code's fault
-- it's not prompt engineering that would do the job, but routing/caching that would come to the rescue
"""

"""
observation --

What I am finding is that the ECG queries (largest dataset) do take longer than the other datasets. Additionally, the same large model (llama-3.3-70b) is used in ReAct throughout. The two ideas I am exploring currently are:

(i) capability routing: matching requests to appropriate models based on task complexity;
(ii) dataset routing: where, for example, the large 30M ECG dataset is filtered down to only the record_id entries actually needed to answer the query -- the filtered dataset is what ReAct is made to execute on instead of the full one.

Comparisons will be made between ReAct with routing and ReAct without routing. The larger model will be used in the latter case at all times. What we would like to answer is whether routing enables us to hit the same accuracy while being faster and cheaper.
"""


## some solutions: prompt-prefix caching, where providers cache repeated static prefixes like the column metadata block, which appears identically in every codegen call. could plausibly cut both the mean and variance of ReAct's latency without touching the control loop.

## *** an intelligent routing layer, where the task is offloaded to the model that is likely to give the fastest response.
## in other words, model selection can be optimized by task complexity. budget-tier models can be used for simple classification and extraction. they can cost 15-50x lesser than the flagship models. the bigger models would be reserved for more complex reasoning, not direct queries.
## capability-based routing matches requests to appropriate models based on task complexity or domain; lightweight models might handle simple queries like basic factual questions, while complex reasoning tasks route to larger, more capable models.
## *** a chunky task can be broken into sub-parts that each of these can be sent to smaller models. these have faster TTFT and TPOT. in other words, routing easy tasks to small models cuts latency on the requests that never needed the big model.

# latency does seem to increase with dataset size as well; ECG takes 10s even for simple responses. A routing layer could initially narrow down the dataset to only what is needed (in addition to the right model for the specific task), and then run execution.

## we could cut the prompt -- trim the initial prompt to be restricted to scope checking; can take care of the predictive intent portion or multi-step breakdown in downstream stages.
## output tokens can be constrained as well. can set max_tokens in LLM calls.

# CACHING: there is an identical prompt prefix passed in every time. a cache hit skips reprocessing the cached portion, which lowers TTFT singificantly on repeated structures. we can structure the messages such that the static content sits at the front.
# [STRETCH] semantic caching for high-traffic, repetitive workloads can also be added in. by storing query vector embeddings and LLM responses, cached answers for semantically similar queries can hit the same cache entry based on similarity threshold.
