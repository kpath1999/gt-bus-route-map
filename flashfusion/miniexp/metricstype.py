"""
Mini-experiment: Model Type Variation

Test Flash-Fusion with different LLM models to verify that results and metrics
remain consistent across model types. This validates that the pipeline is robust
to model selection and not overfitted to a specific model.

Hypothesis: The pipeline should produce consistent accuracy and execution patterns
across different models, though latency and cost may vary by model size.

Models to test:
- meta-llama/llama-3.3-70b-instruct (baseline)
- meta-llama/llama-4-scout-17b-16e-instruct (smaller)
- meta-llama/llama-3.1-8b-instruct (smallest)

Dataset: WISDM or Bus telematics
Queries: Use subset of benchmark queries
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import pandas as pd
import matplotlib.pyplot as plt

from flashfusion.baselines.flash_fusion import run_flash_fusion
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.queries import get_queries
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.config import DEFAULT_MODEL, MODEL_RATE_PER_1M_TOKENS


def run_query_with_model(
    query: str,
    query_id: int,
    df: pd.DataFrame,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Run a query with a specific model and collect metrics.
    
    Args:
        query: Natural language query
        query_id: Query ID for tracking
        df: Input dataframe
        model: LLM model name
        api_key: API key for the LLM provider
        
    Returns:
        Dictionary with performance metrics
    """
    # Initialize client
    client = LLMClient(model_name=model, api_key=api_key)
    result = RunResult()
    
    # Run Flash-Fusion
    try:
        result = run_flash_fusion(query, df, client, result)
        error = None
    except Exception as e:
        error = str(e)
    
    return {
        "query_id": query_id,
        "model": model,
        "executed": result.executed,
        "rejected": result.rejected,
        "answer": result.answer if result.executed else None,
        "stages_run": ",".join(result.stages_run) if result.stages_run else "",
        "num_stages": len(result.stages_run),
        "latency_s": client.total_latency(),
        "cost_usd": client.total_cost_usd(),
        "input_tokens": client.total_input_tokens(),
        "output_tokens": client.total_output_tokens(),
        "num_llm_calls": len(client.call_log),
        "error": error,
    }


def run_experiment(
    dataset_name: str = "wisdm",
    data_path: str | None = None,
    query_ids: list[int] | None = None,
    models: list[str] | None = None,
    output_dir: str = "flashfusion/miniexp/results",
) -> pd.DataFrame:
    """
    Run the model type variation experiment.
    
    Args:
        dataset_name: Name of the dataset to use
        data_path: Path to the dataset file (optional)
        query_ids: List of query IDs to test
        models: List of model names to test
        output_dir: Directory to save results
        
    Returns:
        DataFrame with experiment results
    """
    # Default models to test
    if models is None:
        models = [
            "meta-llama/llama-3.3-70b-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-3.1-8b-instruct",
        ]
    
    # Default queries to test (subset of benchmark)
    if query_ids is None:
        query_ids = [1, 2, 3, 4]  # Test first 4 queries
    
    # Load dataset
    if data_path:
        df = pd.read_csv(data_path)
    else:
        df = load_dataset_by_name(dataset_name)
    
    # Load queries
    queries = get_queries(dataset_name)
    query_map = {q["id"]: q["text"] for q in queries}
    
    # Get API key
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")
    
    # Run experiment for each model and query
    results = []
    for model in models:
        print(f"\nTesting model: {model}")
        for query_id in query_ids:
            if query_id not in query_map:
                print(f"  Query {query_id} not found, skipping...")
                continue
            
            query = query_map[query_id]
            print(f"  Query {query_id}: {query[:60]}...")
            
            try:
                metrics = run_query_with_model(query, query_id, df, model, api_key)
                results.append(metrics)
                print(f"    Executed: {metrics['executed']}, Rejected: {metrics['rejected']}")
                print(f"    Latency: {metrics['latency_s']:.2f}s, Cost: ${metrics['cost_usd']:.4f}")
            except Exception as e:
                print(f"    Error: {e}")
                results.append({
                    "query_id": query_id,
                    "model": model,
                    "executed": False,
                    "rejected": False,
                    "error": str(e),
                })
    
    # Convert to DataFrame
    results_df = pd.DataFrame(results)
    
    # Save results
    os.makedirs(output_dir, exist_ok=True)
    results_df.to_csv(f"{output_dir}/metrics_vs_model_type.csv", index=False)
    print(f"\nResults saved to {output_dir}/metrics_vs_model_type.csv")
    
    return results_df


def analyze_consistency(results_df: pd.DataFrame) -> pd.DataFrame:
    """
    Analyze consistency of results across models.
    
    Args:
        results_df: DataFrame with experiment results
        
    Returns:
        DataFrame with consistency analysis
    """
    # Group by query and check if execution status is consistent
    consistency = []
    
    for query_id in results_df["query_id"].unique():
        query_results = results_df[results_df["query_id"] == query_id]
        
        # Check execution consistency
        exec_values = query_results["executed"].unique()
        rej_values = query_results["rejected"].unique()
        
        consistency.append({
            "query_id": query_id,
            "num_models_tested": len(query_results),
            "execution_consistent": len(exec_values) == 1,
            "executed_by_all": all(query_results["executed"]),
            "rejected_by_all": all(query_results["rejected"]),
            "avg_latency_s": query_results["latency_s"].mean(),
            "std_latency_s": query_results["latency_s"].std(),
            "avg_cost_usd": query_results["cost_usd"].mean(),
            "std_cost_usd": query_results["cost_usd"].std(),
        })
    
    return pd.DataFrame(consistency)


def plot_results(results_df: pd.DataFrame, output_dir: str = "flashfusion/miniexp/results"):
    """
    Plot model comparison results.
    
    Args:
        results_df: DataFrame with experiment results
        output_dir: Directory to save plots
    """
    # Filter out errors
    valid_results = results_df[results_df["error"].isna() | (results_df["error"] == "")]
    
    if valid_results.empty:
        print("No valid results to plot")
        return
    
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # 1. Execution success rate by model
    ax1 = axes[0, 0]
    exec_by_model = valid_results.groupby("model")["executed"].mean()
    exec_by_model.plot(kind="bar", ax=ax1, color="steelblue")
    ax1.set_ylabel("Execution Success Rate")
    ax1.set_title("Query Execution Success by Model")
    ax1.set_ylim([0, 1.1])
    ax1.grid(True, alpha=0.3, axis='y')
    ax1.tick_params(axis='x', rotation=15)
    
    # 2. Average latency by model
    ax2 = axes[0, 1]
    latency_by_model = valid_results.groupby("model")["latency_s"].mean()
    latency_by_model.plot(kind="bar", ax=ax2, color="coral")
    ax2.set_ylabel("Latency (seconds)")
    ax2.set_title("Average Query Latency by Model")
    ax2.grid(True, alpha=0.3, axis='y')
    ax2.tick_params(axis='x', rotation=15)
    
    # 3. Average cost by model
    ax3 = axes[1, 0]
    cost_by_model = valid_results.groupby("model")["cost_usd"].mean()
    cost_by_model.plot(kind="bar", ax=ax3, color="green")
    ax3.set_ylabel("Cost (USD)")
    ax3.set_title("Average Query Cost by Model")
    ax3.grid(True, alpha=0.3, axis='y')
    ax3.tick_params(axis='x', rotation=15)
    
    # 4. Latency vs. Cost scatter by model
    ax4 = axes[1, 1]
    for model in valid_results["model"].unique():
        model_data = valid_results[valid_results["model"] == model]
        ax4.scatter(
            model_data["latency_s"],
            model_data["cost_usd"],
            label=model.split("/")[-1],
            alpha=0.6,
            s=100,
        )
    ax4.set_xlabel("Latency (seconds)")
    ax4.set_ylabel("Cost (USD)")
    ax4.set_title("Latency vs. Cost by Model")
    ax4.legend(fontsize=8)
    ax4.grid(True, alpha=0.3)
    
    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    plt.savefig(f"{output_dir}/metrics_vs_model_type.png", dpi=300)
    print(f"Plot saved to {output_dir}/metrics_vs_model_type.png")
    plt.close()


def main():
    """CLI entry point for the model type variation experiment."""
    parser = argparse.ArgumentParser(description="Model Type Variation Mini-Experiment")
    parser.add_argument("--dataset", default="wisdm", help="Dataset name (default: wisdm)")
    parser.add_argument("--data-path", help="Path to dataset file (optional)")
    parser.add_argument("--query-ids", nargs="+", type=int, help="Query IDs to test")
    parser.add_argument("--models", nargs="+", help="Models to test")
    parser.add_argument("--output-dir", default="flashfusion/miniexp/results", help="Output directory")
    parser.add_argument("--plot", action="store_true", help="Generate plots")
    parser.add_argument("--analyze", action="store_true", help="Analyze consistency")
    
    args = parser.parse_args()
    
    # Run experiment
    results_df = run_experiment(
        dataset_name=args.dataset,
        data_path=args.data_path,
        query_ids=args.query_ids,
        models=args.models,
        output_dir=args.output_dir,
    )
    
    # Analyze consistency
    if args.analyze:
        consistency_df = analyze_consistency(results_df)
        consistency_df.to_csv(f"{args.output_dir}/model_consistency_analysis.csv", index=False)
        print("\nConsistency Analysis:")
        print(consistency_df.to_string(index=False))
    
    # Plot if requested
    if args.plot:
        plot_results(results_df, args.output_dir)


if __name__ == "__main__":
    main()