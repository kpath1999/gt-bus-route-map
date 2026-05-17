# Flash-Fusion Terminal Guide

This guide gives you 74 practical terminal commands to understand the repository, validate it, and run baseline comparisons.

How to use this guide:
1. Run commands from the repository root: /Users/kausar/Documents/flash-fusion
2. Read the explanation under each command before running it
3. Run smoke tests before full benchmarks to avoid long failed runs

1. Command: cd /Users/kausar/Documents/flash-fusion
What it does: Moves your shell into the project root folder.
Why it matters: Most relative paths in this guide assume you are in this directory.

2. Command: pwd
What it does: Prints your current working directory.
Why it matters: Confirms you are in the expected folder before running project commands.

3. Command: ls
What it does: Lists files and folders in the current directory.
Why it matters: Quick sanity check that repo contents are present.

4. Command: ls flashfusion
What it does: Lists top-level package files and folders inside flashfusion.
Why it matters: Gives you a first view of module layout.

5. Command: ls flashfusion/pipeline
What it does: Lists pipeline modules such as stages, executor, and runner.
Why it matters: Helps you locate the core execution path quickly.

6. Command: ls flashfusion/baselines
What it does: Lists baseline implementations.
Why it matters: Shows exactly where each baseline mode lives.

7. Command: ls flashfusion/eval
What it does: Lists evaluation modules (benchmark, metrics, reporter, queries).
Why it matters: Identifies where scoring and report generation happen.

8. Command: find flashfusion -maxdepth 3 -type f | sort
What it does: Prints a sorted file inventory up to 3 levels deep.
Why it matters: Gives a comprehensive but readable map of repository files.

9. Command: tree -L 3 flashfusion
What it does: Shows folder structure in tree form.
Why it matters: Easier than flat lists when learning package hierarchy.

10. Command: rg "class |def |RunResult|BaselineRunner|ExecutionLayer|Stage1_|Stage2_|Stage3_" flashfusion
What it does: Searches for core symbols and function definitions.
Why it matters: Fast way to identify important code entry points.

11. Command: wc -l flashfusion/CLAUDE.md
What it does: Counts lines in CLAUDE.md.
Why it matters: Helps estimate document size before reading it in chunks.

12. Command: sed -n '1,220p' flashfusion/CLAUDE.md
What it does: Prints the first section of CLAUDE.md.
Why it matters: Covers project intent, architecture, and high-level requirements.

13. Command: sed -n '220,520p' flashfusion/CLAUDE.md
What it does: Prints the middle section of CLAUDE.md.
Why it matters: Usually includes interfaces and implementation contracts.

14. Command: sed -n '520,980p' flashfusion/CLAUDE.md
What it does: Prints later sections of CLAUDE.md.
Why it matters: Often includes tests, verification, and pitfalls.

15. Command: rg "Quick Start|System Architecture|Verification Checklist" flashfusion/CLAUDE.md
What it does: Finds key navigation headings in CLAUDE.md.
Why it matters: Lets you jump directly to the most actionable sections.

16. Command: sed -n '1,220p' flashfusion/config.py
What it does: Shows project constants such as model rates and thresholds.
Why it matters: Central place to understand defaults and scoring constants.

17. Command: rg "MODEL_RATE_PER_1M_TOKENS|DEFAULT_MODEL|ACCURACY_" flashfusion/config.py
What it does: Extracts critical configuration variables.
Why it matters: Useful when explaining cost and metric behavior.

18. Command: sed -n '1,220p' flashfusion/eval/queries.py
What it does: Displays benchmark query definitions.
Why it matters: You need this to interpret baseline performance by query type.

19. Command: rg "WISDM_QUERIES|EXPECTED_OUTCOMES" flashfusion/eval/queries.py
What it does: Finds query and expected-behavior structures quickly.
Why it matters: Useful for validating benchmark logic and reports.

20. Command: sed -n '1,220p' flashfusion/prompts/templates.py
What it does: Shows canonical prompts used by pipeline stages.
Why it matters: Prompt behavior strongly influences grounding and decomposition.

21. Command: sed -n '1,220p' flashfusion/pipeline/loader.py
What it does: Shows WISDM loading and metadata generation logic.
Why it matters: Data cleanliness and schema metadata quality start here.

22. Command: sed -n '1,240p' flashfusion/adapters/wisdm_adapter.py
What it does: Shows codebook and derived feature generation.
Why it matters: Explains how magnitude and activity_name are materialized.

23. Command:
python - <<'PY'
import pandas as pd
from flashfusion.pipeline.loader import load_wisdm
from flashfusion.adapters.wisdm_adapter import WISDMAdapter
df = load_wisdm("chat/data/imu/WISDM_ar_v1.1_raw.txt")
print(df.shape)
print(df.dtypes)
adapter = WISDMAdapter()
df2, prov = adapter.get_derived_features(df)
print(df2.columns.tolist())
print(prov)
print(df2[["activity_label","activity_name","magnitude"]].head(5))
PY
What it does: Runs a quick data-and-adapter smoke script.
Why it matters: Verifies loader and adapter behavior before LLM-dependent runs.

24. Command: sed -n '1,260p' flashfusion/pipeline/stages.py
What it does: Prints Stage 1 and part of Stage 2 logic.
Why it matters: Helps you understand concept extraction and grounding pipeline.

25. Command: sed -n '260,520p' flashfusion/pipeline/stages.py
What it does: Prints the rest of Stage 2 and Stage 3 logic.
Why it matters: Shows decomposition into executable sub-queries.

26. Command: rg "S1|S2|S3|guardrail|synthesis|judge" flashfusion/pipeline/stages.py flashfusion/pipeline/executor.py flashfusion/pipeline/runner.py
What it does: Searches stage labels across the orchestration pipeline.
Why it matters: Reveals the end-to-end control flow quickly.

27. Command: sed -n '1,280p' flashfusion/pipeline/executor.py
What it does: Prints parser, callbacks, and early executor logic.
Why it matters: Critical for understanding agent interaction and trace capture.

28. Command: sed -n '280,620p' flashfusion/pipeline/executor.py
What it does: Prints guardrail, execution, judge, synthesis, and reset methods.
Why it matters: These methods define runtime behavior and quality checks.

29. Command: rg "def guardrail|def execute_single|def judge_result|def synthesize|def reset_agent" flashfusion/pipeline/executor.py
What it does: Extracts key executor functions by name.
Why it matters: Great for fast code navigation and explanation prep.

30. Command: sed -n '1,320p' flashfusion/pipeline/runner.py
What it does: Prints LLMClient, RunResult, and BaselineRunner logic.
Why it matters: This is the central dispatcher for baseline execution.

31. Command: rg "class BaselineRunner|def run\(|def _run_" flashfusion/pipeline/runner.py
What it does: Locates dispatch and baseline entry methods.
Why it matters: Helps you map mode names to actual code paths.

32. Command: sed -n '1,240p' flashfusion/baselines/llm_only.py
What it does: Shows the LLM-only baseline implementation.
Why it matters: Establishes a non-executing baseline for comparison.

33. Command: sed -n '1,280p' flashfusion/baselines/wellmax_only.py
What it does: Shows schema-grounded but non-executing baseline.
Why it matters: Isolates the value of grounding without agent execution.

34. Command: sed -n '1,220p' flashfusion/baselines/autoiot_only.py
What it does: Shows execution-focused baseline without full fusion pipeline.
Why it matters: Isolates effect of code execution without full decomposition.

35. Command: sed -n '1,320p' flashfusion/baselines/flash_fusion.py
What it does: Shows full fused pipeline baseline.
Why it matters: Primary system mode to compare against others.

36. Command: rg "def run_llm_only|def run_wellmax_only|def run_autoiot_only|def run_flash_fusion" flashfusion/baselines
What it does: Finds callable baseline functions across files.
Why it matters: Useful for traceability and quick comparisons.

37. Command: sed -n '1,260p' flashfusion/eval/metrics.py
What it does: Shows scoring logic for accuracy, latency, and cost.
Why it matters: You need this to interpret reported numbers correctly.

38. Command: sed -n '1,280p' flashfusion/eval/reporter.py
What it does: Shows report and table generation logic.
Why it matters: Explains how raw run results become summary artifacts.

39. Command: sed -n '1,320p' flashfusion/eval/benchmark.py
What it does: Shows benchmark CLI orchestration logic.
Why it matters: This is the main command-line entry point for experiments.

40. Command: python -m flashfusion.eval.benchmark --help
What it does: Displays CLI usage and options.
Why it matters: Prevents argument mistakes before long runs.

41. Command: python --version
What it does: Prints active Python version.
Why it matters: Confirms you are using the expected interpreter.

42. Command: pip --version
What it does: Prints pip version and install path.
Why it matters: Confirms package install tool matches your Python environment.

43. Command: python -m pip show flashfusion
What it does: Shows package metadata if installed.
Why it matters: Verifies editable install status and install location.

44. Command: pip install -e flashfusion/
What it does: Installs flashfusion in editable mode.
Why it matters: Lets code edits reflect immediately without reinstalling.

45. Command: python -c "import flashfusion; print('import ok')"
What it does: Tests basic package import.
Why it matters: Fast verification that install and import paths work.

46. Command: python -c "from flashfusion.pipeline.runner import BaselineRunner; print('runner ok')"
What it does: Tests import of a core runtime class.
Why it matters: Confirms key module dependencies are resolved.

47. Command: python -m pip list | rg "langchain|groq|pandas|tabulate"
What it does: Shows important installed dependency versions.
Why it matters: Helps diagnose version mismatch issues quickly.

48. Command: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest flashfusion/tests/ -v
What it does: Runs full unit tests with plugin autoload disabled.
Why it matters: Reduces interference from unrelated global pytest plugins.

49. Command: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest flashfusion/tests/test_stages.py -v
What it does: Runs only stage tests.
Why it matters: Useful when debugging Stage 1/2/3 behavior specifically.

50. Command: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest flashfusion/tests/test_executor.py -v
What it does: Runs executor and parser tests.
Why it matters: Verifies guardrail, parser resilience, and judge path behavior.

51. Command: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest flashfusion/tests/test_metrics.py -v
What it does: Runs metric scoring tests only.
Why it matters: Ensures reported accuracy/cost/latency logic is stable.

52. Command: export GROQ_API_KEY="your_real_groq_key_here"
What it does: Sets your API key in current shell session.
Why it matters: Required for live LLM benchmark runs.

53. Command: echo ${#GROQ_API_KEY}
What it does: Prints key length only, not the key itself.
Why it matters: Safe way to confirm key exists without exposing secrets.

54. Command: if [ -n "$GROQ_API_KEY" ]; then echo "GROQ_API_KEY is set"; else echo "GROQ_API_KEY is not set"; fi
What it does: Checks whether key variable is non-empty.
Why it matters: Quick prerequisite check before benchmark execution.

55. Command: rm -rf flashfusion/eval_results
What it does: Removes old smoke benchmark output directory.
Why it matters: Avoids mixing old and new output files.

56. Command: mkdir -p flashfusion/eval_results
What it does: Creates output directory if missing.
Why it matters: Ensures benchmark can write files without directory errors.

57. Command: python -m flashfusion.eval.benchmark --data chat/data/imu/WISDM_ar_v1.1_raw.txt --baselines all --queries 1,4,10 --output flashfusion/eval_results/
What it does: Runs a short smoke benchmark on 3 queries across all baselines.
Why it matters: Fast way to verify end-to-end system health before full run.

58. Command: rm -rf flashfusion/eval_results_full
What it does: Removes previous full benchmark output directory.
Why it matters: Prevents stale files from confusing analysis.

59. Command: mkdir -p flashfusion/eval_results_full
What it does: Creates directory for full benchmark outputs.
Why it matters: Ensures clean destination for full run artifacts.

60. Command: python -m flashfusion.eval.benchmark --data chat/data/imu/WISDM_ar_v1.1_raw.txt --baselines all --queries all --output flashfusion/eval_results_full/
What it does: Runs full benchmark across all defined queries and baselines.
Why it matters: Produces comprehensive comparison data.

61. Command:
python - <<'PY'
import pandas as pd
df = pd.read_csv("flashfusion/eval_results/metrics.csv")
print(df.head())
print("\nBy baseline mean metrics:")
print(df.groupby("baseline")[["accuracy_score","latency_s","cost_usd","input_tokens","output_tokens"]].mean().sort_values("accuracy_score", ascending=False))
print("\nExecution/rejection rates:")
print(df.groupby("baseline")[["executed","rejected"]].mean())
print("\nJudge verdict counts:")
print(df.groupby(["baseline","judge_verdict"]).size())
PY
What it does: Summarizes key metrics by baseline from smoke results.
Why it matters: Converts raw CSV into decision-ready insights.

62. Command:
python - <<'PY'
import pandas as pd
df = pd.read_csv("flashfusion/eval_results/metrics.csv")
pivot = df.pivot_table(index="query_id", columns="baseline", values="accuracy_score", aggfunc="first")
print("Accuracy per query:")
print(pivot)
print("\nLatency per query:")
print(df.pivot_table(index="query_id", columns="baseline", values="latency_s", aggfunc="first"))
print("\nCost per query:")
print(df.pivot_table(index="query_id", columns="baseline", values="cost_usd", aggfunc="first"))
PY
What it does: Builds per-query comparison pivots for accuracy, latency, and cost.
Why it matters: Makes baseline strengths and weaknesses clear per task.

63. Command: ls -lah flashfusion/eval_results
What it does: Lists generated output files with sizes.
Why it matters: Confirms expected artifacts were created.

64. Command: head -n 20 flashfusion/eval_results/raw_results.jsonl
What it does: Prints first 20 lines of raw JSONL run outputs.
Why it matters: Helps inspect individual run records quickly.

65. Command: sed -n '1,220p' flashfusion/eval_results/report.md
What it does: Prints the first report section.
Why it matters: Good for checking readability and summary correctness.

66. Command: csvlook flashfusion/eval_results/metrics.csv
What it does: Pretty-prints metrics.csv in a table format.
Why it matters: Easier visual scanning than raw CSV.

67. Command: for i in 1 2 3; do python -m flashfusion.eval.benchmark --data chat/data/imu/WISDM_ar_v1.1_raw.txt --baselines FLASH_FUSION,AUTOIOT_ONLY --queries 2,3,5,6 --output flashfusion/eval_results_run_$i; done
What it does: Runs three repeated targeted benchmarks for stability checks.
Why it matters: Lets you see variance across repeated runs.

68. Command:
python - <<'PY'
import glob, pandas as pd
paths = glob.glob("flashfusion/eval_results_run_*/metrics.csv")
dfs = [pd.read_csv(p).assign(run=p.split("_")[-1].split("/")[0]) for p in paths]
all_df = pd.concat(dfs, ignore_index=True)
print(all_df.groupby(["baseline"])[["accuracy_score","latency_s","cost_usd"]].agg(["mean","std"]))
PY
What it does: Aggregates repeated-run metrics and prints mean plus standard deviation.
Why it matters: Quantifies consistency, not just one-off performance.

69. Command:
python - <<'PY'
from flashfusion.pipeline.loader import load_wisdm
df = load_wisdm("chat/data/imu/WISDM_ar_v1.1_raw.txt")
print("rows:", len(df))
print("columns:", list(df.columns))
print(df.describe(include='all').transpose().head(10))
PY
What it does: Prints quick dataset profile statistics.
Why it matters: Helps beginners connect query behavior to actual data shape.

70. Command: git status -sb
What it does: Shows concise git status and branch info.
Why it matters: Tracks which files changed during experiments.

71. Command: git diff -- flashfusion/pipeline/executor.py
What it does: Shows code changes in executor module only.
Why it matters: Isolates runtime and parser changes for review.

72. Command: git diff -- flashfusion/pipeline/runner.py
What it does: Shows changes in runner dispatch and mode behavior.
Why it matters: Helps verify baseline-routing logic updates.

73. Command: git diff -- flashfusion/eval/metrics.py
What it does: Shows changes in metrics and aggregation behavior.
Why it matters: Lets you confirm scoring semantics remain correct.

74. Command: git --no-pager log --oneline -n 5
What it does: Shows last 5 commits in one-line format.
Why it matters: Quick project history context before making new changes.

