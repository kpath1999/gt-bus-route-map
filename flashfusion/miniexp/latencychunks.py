"""
Mini-experiment: Latency vs. Chunks

Measure how TTFT (Time To First Token) and E2E (End-to-End) latency change
as we increase the number of data chunks included in the input prompt.

Hypothesis: As chunk count increases, both TTFT and E2E latency should increase
linearly due to increased context length.

Dataset: Bus telematics (or any dataset that can be easily chunked)
Query: Simple aggregation query (e.g., "Find me the average of column X")
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import pandas as pd
import matplotlib.pyplot as plt

from flashfusion.baselines.flash_fusion import run_flash_fusion
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.config import DEFAULT_MODEL


def chunk_dataframe(df: pd.DataFrame, num_chunks: int) -> list[pd.DataFrame]:
    """
    Split a dataframe into approximately equal chunks.
    
    Args:
        df: Input dataframe
        num_chunks: Number of chunks to create
        
    Returns:
        List of dataframe chunks
    """
    chunk_size = len(df) // num_chunks
    chunks = []
    for i in range(num_chunks):
        start_idx = i * chunk_size
        end_idx = start_idx + chunk_size if i < num_chunks - 1 else len(df)
        chunks.append(df.iloc[start_idx:end_idx])
    return chunks


def measure_latency_with_chunks(
    query: str,
    df: pd.DataFrame,
    num_chunks: int,
    model: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Run a query with a specific number of chunks and measure latencies.
    
    Args:
        query: Natural language query
        df: Full dataframe
        num_chunks: Number of chunks to include in context
        model: LLM model name
        api_key: API key for the LLM provider
        
    Returns:
        Dictionary with latency metrics
    """
    # Create chunks
    chunks = chunk_dataframe(df, max(num_chunks, 1))
    
    # Take only the first num_chunks
    chunked_df = pd.concat(chunks[:num_chunks]) if num_chunks > 0 else df.iloc[:0]
    
    # Initialize client
    client = LLMClient(model_name=model, api_key=api_key)
    result = RunResult()
    
    # Measure E2E latency
    start_time = time.time()
    result = run_flash_fusion(query, chunked_df, client, result)
    e2e_latency = time.time() - start_time
    
    # Extract TTFT (approximate as first LLM call latency)
    ttft = client.call_log[0].latency_s if client.call_log else 0.0
    
    return {
        "num_chunks": num_chunks,
        "chunk_rows": len(chunked_df),
        "ttft_ms": ttft * 1000,
        "e2e_latency_ms": e2e_latency * 1000,
        "total_latency_ms": client.total_latency() * 1000,
        "num_llm_calls": len(client.call_log),
    }


def run_experiment(
    dataset_name: str = "bus",
    data_path: str | None = None,
    chunk_counts: list[int] | None = None,
    model: str = DEFAULT_MODEL,
    output_dir: str = "flashfusion/miniexp/results",
) -> pd.DataFrame:
    """
    Run the latency vs. chunks experiment.
    
    Args:
        dataset_name: Name of the dataset to use
        data_path: Path to the dataset file (optional, uses default if not provided)
        chunk_counts: List of chunk counts to test (e.g., [1, 2, 4, 8, 16])
        model: LLM model to use
        output_dir: Directory to save results
        
    Returns:
        DataFrame with experiment results
    """
    # Default chunk counts
    if chunk_counts is None:
        chunk_counts = [1, 2, 4, 8, 16, 32]
    
    # Load dataset
    if data_path:
        df = pd.read_csv(data_path)
    else:
        df = load_dataset_by_name(dataset_name)
    
    # Simple query for testing
    query = "What is the average value of the first numeric column?"
    
    # Get API key
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")
    
    # Run experiment for each chunk count
    results = []
    for num_chunks in chunk_counts:
        print(f"Testing with {num_chunks} chunks...")
        try:
            metrics = measure_latency_with_chunks(query, df, num_chunks, model, api_key)
            results.append(metrics)
            print(f"  TTFT: {metrics['ttft_ms']:.2f}ms, E2E: {metrics['e2e_latency_ms']:.2f}ms")
        except Exception as e:
            print(f"  Error with {num_chunks} chunks: {e}")
            continue
    
    # Convert to DataFrame
    results_df = pd.DataFrame(results)
    
    # Save results
    os.makedirs(output_dir, exist_ok=True)
    results_df.to_csv(f"{output_dir}/latency_vs_chunks.csv", index=False)
    print(f"\nResults saved to {output_dir}/latency_vs_chunks.csv")
    
    return results_df


def plot_results(results_df: pd.DataFrame, output_dir: str = "flashfusion/miniexp/results"):
    """
    Plot latency vs. chunks results.
    
    Args:
        results_df: DataFrame with experiment results
        output_dir: Directory to save plots
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    
    # TTFT vs. chunks
    ax1.plot(results_df["num_chunks"], results_df["ttft_ms"], marker='o')
    ax1.set_xlabel("Number of Chunks")
    ax1.set_ylabel("TTFT (ms)")
    ax1.set_title("Time To First Token vs. Chunks")
    ax1.grid(True, alpha=0.3)
    
    # E2E latency vs. chunks
    ax2.plot(results_df["num_chunks"], results_df["e2e_latency_ms"], marker='o', color='orange')
    ax2.set_xlabel("Number of Chunks")
    ax2.set_ylabel("E2E Latency (ms)")
    ax2.set_title("End-to-End Latency vs. Chunks")
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    plt.savefig(f"{output_dir}/latency_vs_chunks.png", dpi=300)
    print(f"Plot saved to {output_dir}/latency_vs_chunks.png")
    plt.close()


def main():
    """CLI entry point for the latency vs. chunks experiment."""
    parser = argparse.ArgumentParser(description="Latency vs. Chunks Mini-Experiment")
    parser.add_argument("--dataset", default="bus", help="Dataset name (default: bus)")
    parser.add_argument("--data-path", help="Path to dataset file (optional)")
    parser.add_argument("--chunks", nargs="+", type=int, help="Chunk counts to test")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--output-dir", default="flashfusion/miniexp/results", help="Output directory")
    parser.add_argument("--plot", action="store_true", help="Generate plots")
    
    args = parser.parse_args()
    
    # Run experiment
    results_df = run_experiment(
        dataset_name=args.dataset,
        data_path=args.data_path,
        chunk_counts=args.chunks,
        model=args.model,
        output_dir=args.output_dir,
    )
    
    # Plot if requested
    if args.plot:
        plot_results(results_df, args.output_dir)


if __name__ == "__main__":
    main()