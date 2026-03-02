"""
queries.py
----------
Central registry for all evaluation queries, ground-truth functions,
and out-of-scope test cases used by eval.py.
"""

import pandas as pd

# ====================================================
# STANDARD TEST QUERIES
# ====================================================

TEST_QUERIES = [
    "How many rows have accel_mean exactly equal to 9.344?",
    "How many rows have accel_variance greater than 0.15?",
    "Count the data points where accel_stats_z_p99 exceeds 11.0.",
    "What is the maximum value of accel_stats_x_p99 and its corresponding timestamp?",
    "Calculate the average accel_stats_y_p90 across the dataset.",
    "What is the standard deviation of accel_mean?",
    "Count the number of unique latitude-longitude locations.",
    "Find the top 5 most frequent locations by grouping latitude and longitude.",
    "What is the earliest timestamp and its accel_mean value?",
    "How many data points have longitude between -84.39 and -84.38?",
]

# ====================================================
# INTENT QUERIES (conversational / non-analytical)
# Mapped 1:1 to TEST_QUERIES but with looser NL phrasing
# ====================================================

QUERY_INTENT = [
    "How many times was the average acceleration exactly 9.344?",
    "Tell me how often the acceleration variance was higher than 0.15.",
    "Count the moments where the 99th percentile of Z-axis acceleration went above 11.",
    "When did we see the highest 99th percentile X-axis acceleration, and what was that value?",
    "What's the average for the 90th percentile of Y-axis acceleration?",
    "How much does the average acceleration value typically vary?",
    "How many unique places did the bus visit?",
    "Where does the bus spend the most time? Give me the top 5 spots.",
    "When did recording start, and what was the average acceleration then?",
    "How many readings happened when the bus was between longitude -84.39 and -84.38?",
]

# ====================================================
# OUT-OF-SCOPE QUERIES (should be rejected by guardrail)
# ====================================================

OUT_OF_SCOPE = [
    # missing columns
    "What was the average vehicle speed during high accel_variance?",
    "At what timestamps did the battery level drop below 20%?",
    "How many unique driver IDs are in the dataset?",
    "What is the gyroscope mean value for accel_mean = 9.344?",
    # external data required
    "What was the weather at the location with maximum accel_stats_z_p99?",
    "Calculate total distance traveled between consecutive timestamps.",
    "Did the vehicle stop at any traffic lights based on longitude?",
    # impossible derivations
    "How many potholes were hit based on accel_stats_x_p90 spikes?",
    "Predict the next latitude from current acceleration trends.",
    "What is the fuel efficiency during periods of low accel_variance?",
]

# ====================================================
# Ground-truth computations (one per TEST_QUERY, same order)
# ====================================================

def gt_accel_mean_exact(df):
    count = (df["accel_mean"] == 9.344).sum()
    return str(count)

def gt_variance_above(df):
    count = (df["accel_variance"] > 0.15).sum()
    return str(count)

def gt_z_p99_above(df):
    count = (df["accel_stats_z_p99"] > 11.0).sum()
    return str(count)

def gt_max_x_p99(df):
    idx = df["accel_stats_x_p99"].idxmax()
    return f"{df.loc[idx, 'accel_stats_x_p99']} at {df.loc[idx, 'timestamp']}"

def gt_avg_y_p90(df):
    return f"{df['accel_stats_y_p90'].mean():.4f}"

def gt_std_accel_mean(df):
    return f"{df['accel_mean'].std():.6f}"

def gt_unique_locations(df):
    count = len(df[["latitude", "longitude"]].drop_duplicates())
    return str(count)

def gt_top5_locations(df):
    top5 = df.groupby(["latitude", "longitude"]).size().nlargest(5).reset_index(name="count")
    return top5.to_string(index=False)

def gt_earliest_timestamp(df):
    row = df.sort_values("timestamp").iloc[0]
    return f"timestamp={row['timestamp']}, accel_mean={row['accel_mean']}"

def gt_lon_range(df):
    count = df[(df["longitude"] >= -84.39) & (df["longitude"] <= -84.38)].shape[0]
    return str(count)

GROUND_TRUTH_FNS = [
    gt_accel_mean_exact,
    gt_variance_above,
    gt_z_p99_above,
    gt_max_x_p99,
    gt_avg_y_p90,
    gt_std_accel_mean,
    gt_unique_locations,
    gt_top5_locations,
    gt_earliest_timestamp,
    gt_lon_range,
]

# Ground truth responses for out-of-scope queries — all should indicate insufficient data
GT_OUT_OF_SCOPE = [
    "Dataset lacks vehicle speed column and deriving speed via double-integration is not feasible",
    "Dataset lacks battery level column - cannot track battery status",
    "Dataset lacks driver ID column - cannot count unique drivers",
    "Dataset lacks gyroscope data - only has accelerometer statistics",
    "Dataset lacks weather information - cannot correlate with external weather data",
    "Dataset lacks detailed positional tracking - cannot calculate distance between sparse GPS points",
    "Dataset lacks traffic infrastructure data - cannot identify traffic light locations",
    "Dataset lacks road condition sensors - cannot detect potholes from acceleration alone",
    "Dataset lacks sufficient temporal density - cannot reliably predict future positions",
    "Dataset lacks engine/fuel consumption data - cannot determine fuel efficiency",
]

OTHERS = [
    "What is the overall distribution of the x-axis acceleration percentiles (p1, p10, p90, p99)?",
    "Which axis (x, y, or z) shows the highest 99th percentile values on average?",
    "What are the minimum, median, and maximum values for each percentile column across the dataset?",
    "How often do the 99th percentile values exceed a chosen threshold (e.g., 2 g) on each axis?",
    "Which axis has the most skewed distribution, comparing p1, p10, p90, and p99?",
    "How do the 99th percentile values on each axis change over time during a single trip?",
    "During which time windows do we see spikes in accel_stats_y_p99 compared to accel_stats_x_p99 and accel_stats_z_p99?",
    "Can you compute hourly averages of accel_stats_x_p90 and show which hours are roughest?",
    "Identify periods where accel_stats_z_p99 stays unusually high for more than N consecutive samples.",
    "Compare the early part vs. late part of the trip: does accel_stats_x_p99 increase as the trip progresses?",
    "When accel_stats_x_p99 is high, what are the typical values of accel_stats_y_p99 and accel_stats_z_p99?",
    "Are there strong correlations between x, y, and z 99th percentile accelerations?",
    "Are high accel_stats_y_p90 events usually accompanied by high accel_stats_z_p90, or do they occur independently?",
    "Cluster records based on (accel_stats_x_p90, accel_stats_y_p90, accel_stats_z_p90) and describe the clusters.",
    "Find segments where one axis is calm (low p99) while another axis is extreme (high p99).",
    "Find the top 1% most extreme events based on a combined score of x, y, and z 99th percentiles.",
    "Detect bursts where accel_stats_x_p99 exceeds some threshold at least 3 times in a rolling window.",
    "Identify 'transition points' where accel_stats_z_p90 suddenly jumps compared to the previous window.",
    "Mark periods that look like heavy braking or sharp turns, based on patterns in x and y percentiles.",
    "Find outliers where accel_stats_x_p1 and accel_stats_x_p99 are both extreme (possible sensor issues or very rough segments).",
    "Split the data into equal-length segments and compare average accel_stats_z_p99 in each segment.",
    "For each segment, compute the ratio between high-end and low-end values, e.g., accel_stats_x_p99 / |accel_stats_x_p1|, and find segments with the largest ratios.",
    "Identify segments with consistently low accel_stats_y_p90 across all samples (smooth lateral motion).",
    "Rank segments by 'roughness' using a custom metric that combines all three axes' p90/p99 values.",
    "Group data by 'intensity level' bins (low, medium, high based on p99) and report how much time is spent in each level.",
    "Compute summary statistics (mean, std, kurtosis) for each percentile column and highlight which columns are most heavy-tailed.",
    "Fit a simple model to predict accel_stats_z_p99 from the other percentile features; which inputs are most important?",
    "Perform PCA on all percentile columns; how many components explain most of the variability?",
    "Compare the variability (standard deviation) of accel_stats_x_p10 vs accel_stats_x_p90; which side of the distribution is more volatile?",
    "Build a composite 'comfort score' from all percentile values and find the worst and best-scoring samples.",
    "Are there any obvious anomalies, such as accel_stats_x_p1 being greater than accel_stats_x_p90 in some rows?",
    "How many rows have identical values across multiple percentiles (e.g., p1 = p10 = p90 = p99)?",
    "Do the percentile values for each axis behave monotonically (p1 <= p10 <= p90 <= p99) across the dataset?",
    "Are there suspicious constant plateaus in any of the percentile columns, suggesting sensor saturation?",
    "Compare the distribution of accel_stats_z_p1 and accel_stats_z_p99 to see if the range is realistic.",
]
