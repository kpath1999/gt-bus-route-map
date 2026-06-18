"""
Mini-experiment: Dataset Size vs. Query Accuracy

Investigate how query accuracy changes as we vary the dataset size.
This experiment tests whether Flash-Fusion maintains accuracy with larger datasets
or if accuracy degrades due to increased complexity.

Hypothesis: Accuracy should remain relatively stable across different dataset sizes
for well-grounded queries, though latency may increase.

Dataset: WISDM or Bus telematics
Queries: Use standard benchmark queries with adjusted ground truth
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import pandas as pd
import matplotlib.pyplot as plt

from flashfusion.baselines.flash_fusion import run_flash_fusion
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.metrics import compute_accuracy_score
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.config import DEFAULT_MODEL


def sample_dataset(df: pd.DataFrame, sample_fraction: float) -> pd.DataFrame:
    """
    Sample a fraction of the dataset.
    
    Args:
        df: Input dataframe
        sample_fraction: Fraction of data to keep (0.0 to 1.0)
        
    Returns:
        Sampled dataframe
    """
    if sample_fraction >= 1.0:
        return df
    return df.sample(frac=sample_fraction, random_state=42).sort_index()


def compute_ground_truth_for_sample(
    query: str,
    df: pd.DataFrame,
    original_ground_truth: Any,
) -> Any:
    """
    Compute ground truth for a sampled dataset.
    
    For simple aggregations, this may require re-computing the expected value.
    For more complex queries, ground truth logic may need adjustment.
    
    Args:
        query: Natural language query
        df: Sampled dataframe
        original_ground_truth: Original ground truth value
        
    Returns:
        Ground truth for the sampled data
        
    Note: This is a placeholder - actual implementation depends on query type.
    """
    # TODO: Implement query-specific ground truth computation
    # For now, return a placeholder
    return {
        "value": "PLACEHOLDER - needs implementation per query type",
        "note": f"Original GT: {original_ground_truth}, Sample size: {len(df)}",
    }


def run_query_with_sample(
    query: str,
    df: pd.DataFrame,
    sample_fraction: float,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Run a query on a sampled dataset and measure performance.
    
    Args:
        query: Natural language query
        df: Full dataframe
        sample_fraction: Fraction of data to use (0.0 to 1.0)
        model: LLM model name
        api_key: API key for the LLM provider
        
    Returns:
        Dictionary with performance metrics
    """
    # Sample the dataset
    sampled_df = sample_dataset(df, sample_fraction)
    
    # Initialize client
    client = LLMClient(model_name=model, api_key=api_key)
    result = RunResult()
    
    # Run Flash-Fusion
    result = run_flash_fusion(query, sampled_df, client, result)
    
    return {
        "sample_fraction": sample_fraction,
        "sample_rows": len(sampled_df),
        "executed": result.executed,
        "answer": result.answer,
        "latency_s": client.total_latency(),
        "cost_usd": client.total_cost_usd(),
        "num_stages": len(result.stages_run),
    }


def run_experiment(
    dataset_name: str = "wisdm",
    data_path: str | None = None,
    query_id: int = 1,
    sample_fractions: list[float] | None = None,
    model: str = DEFAULT_MODEL,
    output_dir: str = "flashfusion/miniexp/results",
) -> pd.DataFrame:
    """
    Run the dataset size vs. accuracy experiment.
    
    Args:
        dataset_name: Name of the dataset to use
        data_path: Path to the dataset file (optional)
        query_id: Query ID from benchmark queries
        sample_fractions: List of sample fractions to test (0.0 to 1.0)
        model: LLM model to use
        output_dir: Directory to save results
        
    Returns:
        DataFrame with experiment results
    """
    # Default sample fractions
    if sample_fractions is None:
        sample_fractions = [0.1, 0.25, 0.5, 0.75, 1.0]
    
    # Load dataset
    if data_path:
        df = pd.read_csv(data_path)
    else:
        df = load_dataset_by_name(dataset_name)
    
    # Load ground truth for the dataset
    try:
        ground_truth_data = load_ground_truth(dataset_name)
        query_gt = ground_truth_data.get(str(query_id), {})
        query_text = query_gt.get("query", f"Query {query_id}")
    except Exception as e:
        print(f"Warning: Could not load ground truth: {e}")
        query_text = "What is the maximum recorded x-acceleration?"
        query_gt = {}
    
    # Get API key
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")
    
    # Run experiment for each sample fraction
    results = []
    for fraction in sample_fractions:
        print(f"Testing with {fraction*100:.1f}% of data ({int(len(df)*fraction)} rows)...")
        try:
            metrics = run_query_with_sample(query_text, df, fraction, model, api_key)
            results.append(metrics)
            print(f"  Executed: {metrics['executed']}, Answer: {metrics['answer']}")
            print(f"  Latency: {metrics['latency_s']:.2f}s, Cost: ${metrics['cost_usd']:.4f}")
        except Exception as e:
            print(f"  Error with {fraction*100:.1f}% sample: {e}")
            continue
    
    # Convert to DataFrame
    results_df = pd.DataFrame(results)
    
    # Save results
    os.makedirs(output_dir, exist_ok=True)
    results_df.to_csv(f"{output_dir}/accuracy_vs_size.csv", index=False)
    print(f"\nResults saved to {output_dir}/accuracy_vs_size.csv")
    
    return results_df


def plot_results(results_df: pd.DataFrame, output_dir: str = "flashfusion/miniexp/results"):
    """
    Plot accuracy and latency vs. dataset size.
    
    Args:
        results_df: DataFrame with experiment results
        output_dir: Directory to save plots
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    # Execution rate vs. sample size
    ax1.plot(results_df["sample_rows"], results_df["executed"].astype(int), marker='o')
    ax1.set_xlabel("Dataset Size (rows)")
    ax1.set_ylabel("Executed (0/1)")
    ax1.set_title("Query Execution Success vs. Dataset Size")
    ax1.grid(True, alpha=0.3)
    
    # Latency vs. sample size
    ax2.plot(results_df["sample_rows"], results_df["latency_s"], marker='o', color='orange')
    ax2.set_xlabel("Dataset Size (rows)")
    ax2.set_ylabel("Latency (seconds)")
    ax2.set_title("Query Latency vs. Dataset Size")
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    plt.savefig(f"{output_dir}/accuracy_vs_size.png", dpi=300)
    print(f"Plot saved to {output_dir}/accuracy_vs_size.png")
    plt.close()


def main():
    """CLI entry point for the dataset size vs. accuracy experiment."""
    parser = argparse.ArgumentParser(description="Dataset Size vs. Accuracy Mini-Experiment")
    parser.add_argument("--dataset", default="wisdm", help="Dataset name (default: wisdm)")
    parser.add_argument("--data-path", help="Path to dataset file (optional)")
    parser.add_argument("--query-id", type=int, default=1, help="Query ID from benchmark")
    parser.add_argument("--fractions", nargs="+", type=float, help="Sample fractions to test")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--output-dir", default="flashfusion/miniexp/results", help="Output directory")
    parser.add_argument("--plot", action="store_true", help="Generate plots")
    
    args = parser.parse_args()
    
    # Run experiment
    results_df = run_experiment(
        dataset_name=args.dataset,
        data_path=args.data_path,
        query_id=args.query_id,
        sample_fractions=args.fractions,
        model=args.model,
        output_dir=args.output_dir,
    )
    
    # Plot if requested
    if args.plot:
        plot_results(results_df, args.output_dir)


if __name__ == "__main__":
    main()