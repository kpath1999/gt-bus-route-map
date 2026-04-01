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

# ====================================================
# ABSTRACT / OPEN-ENDED QUERIES
# ====================================================
# These are what an everyday user would actually ask.
# No single "right" answer exists; the system must:
#   (i)   identify the abstract concept(s) in the query,
#   (ii)  decompose the query into concrete sub-questions
#         grounded in the dataset schema,
#   (iii) execute the sub-questions, and
#   (iv)  synthesize a coherent natural-language response.
#
# Each entry is a tuple:
#   (query, decomposition_spec)
#
# decomposition_spec is a dict describing:
#   "concepts"    – the abstract ideas the user is expressing
#   "sub_queries" – concrete, column-grounded questions the system
#                   should ask to answer the abstract query
#   "synthesis"   – guidance on how to combine sub-answers
# ====================================================

ABSTRACT_QUERIES = [
    # --- Ride quality ---
    (
        "Was it a bumpy ride?",
        {
            "concepts": ["ride bumpiness", "vibration intensity"],
            "sub_queries": [
                "What is the mean and standard deviation of accel_variance across the trip?",
                "What fraction of data points have accel_variance above the 90th percentile?",
                "What are the max values of accel_stats_z_p99 and accel_stats_x_p99?",
            ],
            "synthesis": (
                "Combine overall accel_variance statistics with extreme percentile "
                "peaks to characterize ride roughness on a qualitative scale "
                "(smooth / moderate / rough / very rough)."
            ),
        },
    ),
    (
        "How comfortable was this trip for passengers?",
        {
            "concepts": ["passenger comfort", "ride smoothness"],
            "sub_queries": [
                "What is the average accel_variance across the entire dataset?",
                "How many data points have accel_stats_y_p99 above 5.0 (sharp lateral force)?",
                "What is the standard deviation of accel_stats_z_p99 (vertical jolt consistency)?",
            ],
            "synthesis": (
                "Low average variance + few lateral/vertical spikes → comfortable. "
                "Quantify a proxy 'comfort score' and translate to plain language."
            ),
        },
    ),
    # --- Safety / driving behavior ---
    (
        "Were there any dangerous driving moments?",
        {
            "concepts": ["dangerous driving", "sudden braking", "sharp turns"],
            "sub_queries": [
                "Find timestamps where accel_stats_x_p99 exceeds 2 standard deviations above its mean.",
                "Find timestamps where accel_stats_y_p99 exceeds 2 standard deviations above its mean.",
                "How many such events occurred, and are they clustered in time?",
            ],
            "synthesis": (
                "Identify extreme X-axis events (hard braking/acceleration) and "
                "extreme Y-axis events (sharp turns). Report count, severity, and "
                "whether they cluster in a specific part of the trip."
            ),
        },
    ),
    (
        "Is the driver driving aggressively?",
        {
            "concepts": ["aggressive driving", "acceleration patterns"],
            "sub_queries": [
                "What is the ratio of accel_stats_x_p99 to accel_stats_x_p1 on average (acceleration range)?",
                "What fraction of readings show accel_variance above the dataset median?",
                "Are there sustained periods of high accel_stats_x_p90?",
            ],
            "synthesis": (
                "High p99/p1 ratio + frequent above-median variance + sustained high "
                "p90 suggest aggressive driving. Classify as calm / normal / aggressive."
            ),
        },
    ),
    # --- Route / road quality ---
    (
        "Which parts of the route need road maintenance?",
        {
            "concepts": ["road quality", "potholes", "rough segments"],
            "sub_queries": [
                "Group by latitude-longitude and compute mean accel_variance per location.",
                "Identify the top 10 locations with the highest mean accel_variance.",
                "At those locations, what are the accel_stats_z_p99 values (vertical shock)?",
            ],
            "synthesis": (
                "High vertical shock (z-axis p99) combined with high variance at specific "
                "GPS coordinates suggests poor road surface. List the coordinates and "
                "describe relative severity."
            ),
        },
    ),
    (
        "Where are the roughest stretches of road?",
        {
            "concepts": ["road roughness", "spatial patterns"],
            "sub_queries": [
                "Compute a combined roughness score = accel_variance + accel_stats_z_p99 per location.",
                "Return the top 5 latitude-longitude pairs by roughness score.",
                "What time windows correspond to those rough locations?",
            ],
            "synthesis": (
                "Map the roughest GPS coordinates and describe them relative to the "
                "trip timeline. Provide a rough-to-smooth ranking."
            ),
        },
    ),
    # --- Temporal patterns ---
    (
        "When during the trip was it most uncomfortable?",
        {
            "concepts": ["temporal discomfort", "time-varying ride quality"],
            "sub_queries": [
                "Split the trip into 10 equal time segments and compute mean accel_variance per segment.",
                "Which segment has the highest mean accel_variance?",
                "In that segment, what is the max accel_stats_z_p99?",
            ],
            "synthesis": (
                "Identify the time window with the worst ride quality and describe "
                "what the sensor readings looked like during that window."
            ),
        },
    ),
    (
        "Did the ride get worse over time?",
        {
            "concepts": ["temporal trend", "degradation"],
            "sub_queries": [
                "Compute accel_variance mean for the first quarter vs. last quarter of timestamps.",
                "Is there a positive trend in accel_stats_z_p99 over time?",
                "Compute Spearman correlation between row index and accel_variance.",
            ],
            "synthesis": (
                "Compare early-trip vs. late-trip variance and check for monotonic trends. "
                "Answer whether the ride quality degraded, improved, or stayed flat."
            ),
        },
    ),
    # --- Trip overview / summary ---
    (
        "Give me a quick summary of this trip.",
        {
            "concepts": ["trip summary", "overall statistics", "highlights"],
            "sub_queries": [
                "How long did the trip last (earliest to latest timestamp)?",
                "How many unique GPS locations were recorded?",
                "What is the overall average and max accel_variance?",
                "Were there any notable spikes in any axis p99 values?",
            ],
            "synthesis": (
                "Produce a 3-4 sentence summary covering duration, route extent, "
                "overall ride quality, and any notable events."
            ),
        },
    ),
    (
        "What's the overall vibe of this ride?",
        {
            "concepts": ["overall impression", "ride character"],
            "sub_queries": [
                "What is the mean and std of accel_mean?",
                "What fraction of points have accel_variance above 0.15?",
                "What is the range (max - min) of accel_stats_z_p99?",
            ],
            "synthesis": (
                "Translate aggregate statistics into a qualitative characterization: "
                "e.g., 'mostly smooth with occasional jolts' or 'consistently rough'."
            ),
        },
    ),
    # --- Anomaly / unusual events ---
    (
        "Was anything unusual about this trip?",
        {
            "concepts": ["anomalies", "outliers", "unexpected patterns"],
            "sub_queries": [
                "Are there any accel_variance values more than 3 standard deviations from the mean?",
                "Are there any sudden GPS jumps (large change in lat/lon between consecutive rows)?",
                "Are there timestamps with accel_stats_x_p99 and accel_stats_z_p99 both in the top 1%?",
            ],
            "synthesis": (
                "Report whether any clear anomalies exist. Distinguish between "
                "sensor anomalies (GPS jumps) and real-world events (extreme accel). "
                "If nothing unusual, say so."
            ),
        },
    ),
    (
        "Did the bus hit any big bumps or potholes?",
        {
            "concepts": ["impact events", "potholes", "sudden vertical shock"],
            "sub_queries": [
                "Find rows where accel_stats_z_p99 exceeds its mean + 2 * std.",
                "At those rows, what are the GPS coordinates?",
                "How many such events occurred and how far apart in time were they?",
            ],
            "synthesis": (
                "Z-axis spikes indicate vertical impacts (bumps/potholes). Report "
                "how many, where, and whether they were isolated or clustered."
            ),
        },
    ),
    # --- Comparative / relative ---
    (
        "Was this a particularly rough route compared to what's normal?",
        {
            "concepts": ["relative roughness", "baseline comparison"],
            "sub_queries": [
                "What are the dataset-wide mean and std of accel_variance?",
                "What fraction of data points exceed mean + 1 std of accel_variance?",
                "Compare the median accel_variance to the dataset mean.",
            ],
            "synthesis": (
                "Since we only have one trip, compare the distribution shape to "
                "expected norms. If >30% of points exceed mean+1std, the route is "
                "rougher than a symmetric distribution would suggest."
            ),
        },
    ),
    # --- Open-ended exploratory ---
    (
        "What can you tell me about the acceleration patterns on this route?",
        {
            "concepts": ["acceleration characterization", "multi-axis patterns"],
            "sub_queries": [
                "Compute mean, std, min, max for accel_mean and accel_variance.",
                "Which axis (x, y, z) has the highest average p99 value?",
                "Is there a correlation between accel_variance and accel_stats_z_p99?",
                "Are there distinct clusters in the accel_variance distribution?",
            ],
            "synthesis": (
                "Provide a multi-faceted characterization: dominant axis, variance "
                "distribution shape, and whether high-variance moments correlate "
                "with specific axes."
            ),
        },
    ),
    (
        "How's the driving quality on this route?",
        {
            "concepts": ["driving quality", "smoothness", "consistency"],
            "sub_queries": [
                "What is the coefficient of variation (std/mean) of accel_mean?",
                "How many abrupt changes in accel_stats_x_p99 occur (diff > 1.0 between consecutive rows)?",
                "What percentage of the trip has accel_variance below the 25th percentile (calm driving)?",
            ],
            "synthesis": (
                "Low CV + few abrupt changes + high calm-percentage → good driving. "
                "Classify and explain in plain terms."
            ),
        },
    ),
]


# ====================================================
# Ground-truth evaluation functions for ABSTRACT_QUERIES
# ====================================================
# For abstract queries there is no single correct number.
# Instead, each ground-truth function returns a structured
# dict with the concrete sub-answers that a correct response
# should cover. The eval harness can check whether the LLM
# response *addresses* these sub-answers rather than matching
# an exact string.
# ====================================================

def _gt_bumpy_ride(df):
    mean_var = df["accel_variance"].mean()
    std_var = df["accel_variance"].std()
    frac_above_p90 = (df["accel_variance"] > df["accel_variance"].quantile(0.9)).mean()
    max_z = df["accel_stats_z_p99"].max()
    max_x = df["accel_stats_x_p99"].max()
    return {
        "mean_accel_variance": round(mean_var, 4),
        "std_accel_variance": round(std_var, 4),
        "frac_above_p90": round(frac_above_p90, 4),
        "max_z_p99": round(max_z, 4),
        "max_x_p99": round(max_x, 4),
        "qualitative": "rough" if mean_var > 0.15 else "moderate" if mean_var > 0.08 else "smooth",
    }

def _gt_comfort(df):
    avg_var = df["accel_variance"].mean()
    lateral_spikes = (df["accel_stats_y_p99"] > 5.0).sum()
    z_std = df["accel_stats_z_p99"].std()
    return {
        "avg_accel_variance": round(avg_var, 4),
        "lateral_spike_count": int(lateral_spikes),
        "z_p99_std": round(z_std, 4),
    }

def _gt_dangerous_moments(df):
    x_mean, x_std = df["accel_stats_x_p99"].mean(), df["accel_stats_x_p99"].std()
    y_mean, y_std = df["accel_stats_y_p99"].mean(), df["accel_stats_y_p99"].std()
    x_extreme = df[df["accel_stats_x_p99"] > x_mean + 2 * x_std]
    y_extreme = df[df["accel_stats_y_p99"] > y_mean + 2 * y_std]
    return {
        "x_extreme_count": len(x_extreme),
        "y_extreme_count": len(y_extreme),
        "x_threshold": round(x_mean + 2 * x_std, 4),
        "y_threshold": round(y_mean + 2 * y_std, 4),
    }

def _gt_aggressive_driving(df):
    ratio = (df["accel_stats_x_p99"] / df["accel_stats_x_p1"].abs().replace(0, float("nan"))).mean()
    frac_above_median = (df["accel_variance"] > df["accel_variance"].median()).mean()
    return {
        "avg_p99_p1_ratio": round(ratio, 4),
        "frac_above_median_variance": round(frac_above_median, 4),
    }

def _gt_road_maintenance(df):
    grouped = df.groupby(["latitude", "longitude"]).agg(
        mean_var=("accel_variance", "mean"),
        mean_z_p99=("accel_stats_z_p99", "mean"),
    ).nlargest(10, "mean_var").reset_index()
    return {
        "top10_locations": grouped[["latitude", "longitude", "mean_var", "mean_z_p99"]].to_dict("records"),
    }

def _gt_roughest_stretches(df):
    df2 = df.copy()
    df2["roughness"] = df2["accel_variance"] + df2["accel_stats_z_p99"]
    top5 = df2.nlargest(5, "roughness")[["latitude", "longitude", "roughness", "timestamp"]]
    return {"top5": top5.to_dict("records")}

def _gt_when_uncomfortable(df):
    df2 = df.copy().sort_values("timestamp").reset_index(drop=True)
    segment_size = len(df2) // 10 or 1
    df2["segment"] = df2.index // segment_size
    seg_stats = df2.groupby("segment")["accel_variance"].mean()
    worst_seg = seg_stats.idxmax()
    return {
        "worst_segment": int(worst_seg),
        "worst_segment_mean_variance": round(seg_stats[worst_seg], 4),
        "segment_count": 10,
    }

def _gt_ride_worse_over_time(df):
    df2 = df.copy().sort_values("timestamp").reset_index(drop=True)
    n = len(df2)
    q1_mean = df2.iloc[: n // 4]["accel_variance"].mean()
    q4_mean = df2.iloc[3 * n // 4 :]["accel_variance"].mean()
    corr = df2["accel_variance"].corr(pd.Series(range(n)))
    return {
        "first_quarter_mean_var": round(q1_mean, 4),
        "last_quarter_mean_var": round(q4_mean, 4),
        "trend_correlation": round(corr, 4),
        "degraded": q4_mean > q1_mean * 1.1,
    }

def _gt_trip_summary(df):
    duration_str = f"{df['timestamp'].min()} to {df['timestamp'].max()}"
    n_locations = len(df[["latitude", "longitude"]].drop_duplicates())
    return {
        "duration": duration_str,
        "unique_locations": n_locations,
        "avg_accel_variance": round(df["accel_variance"].mean(), 4),
        "max_accel_variance": round(df["accel_variance"].max(), 4),
    }

def _gt_overall_vibe(df):
    frac_high_var = (df["accel_variance"] > 0.15).mean()
    z_range = df["accel_stats_z_p99"].max() - df["accel_stats_z_p99"].min()
    return {
        "accel_mean_mean": round(df["accel_mean"].mean(), 4),
        "accel_mean_std": round(df["accel_mean"].std(), 4),
        "frac_high_variance": round(frac_high_var, 4),
        "z_p99_range": round(z_range, 4),
    }

def _gt_unusual(df):
    var_mean, var_std = df["accel_variance"].mean(), df["accel_variance"].std()
    outlier_count = (df["accel_variance"] > var_mean + 3 * var_std).sum()
    # GPS jumps
    lat_diff = df["latitude"].diff().abs()
    lon_diff = df["longitude"].diff().abs()
    gps_jumps = ((lat_diff > 0.01) | (lon_diff > 0.01)).sum()
    return {
        "variance_outlier_count": int(outlier_count),
        "gps_jump_count": int(gps_jumps),
        "variance_3std_threshold": round(var_mean + 3 * var_std, 4),
    }

def _gt_potholes(df):
    z_mean = df["accel_stats_z_p99"].mean()
    z_std = df["accel_stats_z_p99"].std()
    threshold = z_mean + 2 * z_std
    hits = df[df["accel_stats_z_p99"] > threshold]
    return {
        "impact_count": len(hits),
        "threshold": round(threshold, 4),
        "locations": hits[["latitude", "longitude"]].drop_duplicates().values.tolist()[:5],
    }

def _gt_relative_roughness(df):
    mean_var = df["accel_variance"].mean()
    std_var = df["accel_variance"].std()
    frac_above_1std = (df["accel_variance"] > mean_var + std_var).mean()
    median_var = df["accel_variance"].median()
    return {
        "mean_variance": round(mean_var, 4),
        "std_variance": round(std_var, 4),
        "frac_above_mean_plus_1std": round(frac_above_1std, 4),
        "median_variance": round(median_var, 4),
        "rougher_than_normal": frac_above_1std > 0.30,
    }

def _gt_accel_patterns(df):
    x_avg_p99 = df["accel_stats_x_p99"].mean()
    y_avg_p99 = df["accel_stats_y_p99"].mean()
    z_avg_p99 = df["accel_stats_z_p99"].mean()
    dominant = max([("x", x_avg_p99), ("y", y_avg_p99), ("z", z_avg_p99)], key=lambda t: t[1])
    corr_var_z = df["accel_variance"].corr(df["accel_stats_z_p99"])
    return {
        "accel_mean_stats": {"mean": round(df["accel_mean"].mean(), 4), "std": round(df["accel_mean"].std(), 4)},
        "dominant_axis": dominant[0],
        "dominant_axis_avg_p99": round(dominant[1], 4),
        "variance_z_p99_correlation": round(corr_var_z, 4),
    }

def _gt_driving_quality(df):
    cv = df["accel_mean"].std() / df["accel_mean"].mean() if df["accel_mean"].mean() != 0 else float("nan")
    abrupt = (df["accel_stats_x_p99"].diff().abs() > 1.0).sum()
    calm_pct = (df["accel_variance"] < df["accel_variance"].quantile(0.25)).mean()
    return {
        "coefficient_of_variation": round(cv, 4),
        "abrupt_x_changes": int(abrupt),
        "calm_driving_pct": round(calm_pct, 4),
    }


ABSTRACT_GROUND_TRUTH_FNS = [
    _gt_bumpy_ride,
    _gt_comfort,
    _gt_dangerous_moments,
    _gt_aggressive_driving,
    _gt_road_maintenance,
    _gt_roughest_stretches,
    _gt_when_uncomfortable,
    _gt_ride_worse_over_time,
    _gt_trip_summary,
    _gt_overall_vibe,
    _gt_unusual,
    _gt_potholes,
    _gt_relative_roughness,
    _gt_accel_patterns,
    _gt_driving_quality,
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
