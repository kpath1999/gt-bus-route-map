"""
adapters/wisdm_adapter.py — Domain-specific enrichment for the WISDM dataset.

The WISDMAdapter:
  1. Provides the activity codebook (letter code → English name) for injection
     into Stage 2's SCHEMA_GROUNDING_PROMPT.
  2. Materialises derived columns (magnitude, activity_name) onto the DataFrame
     before it is passed to the pandas agent.

The ACTIVITY_CODEBOOK dict is complete. Implement the class methods following
the specs in CLAUDE.md.
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Activity codebook — complete; do not modify.
# Source: chat/data/imu/wisdm-dataset/activity_key.txt
# ---------------------------------------------------------------------------
ACTIVITY_CODEBOOK: dict[str, dict[str, str]] = {
    "activity_label": {
        "A": "Walking",
        "B": "Jogging",
        "C": "Stairs",
        "D": "Sitting",
        "E": "Standing",
        "F": "Typing",
        "G": "Brushing Teeth",
        "H": "Eating Soup",
        "I": "Eating Chips",
        "J": "Eating Pasta",
        "K": "Drinking",
        "L": "Eating Sandwich",
        "M": "Kicking Soccer Ball",
        "O": "Playing Catch",
        "P": "Dribbling Basketball",
        "Q": "Writing",
        "R": "Clapping",
        "S": "Folding Clothes",
    }
}


class WISDMAdapter:
    """
    Domain-specific enrichment adapter for the WISDM activity recognition dataset.

    Usage in BaselineRunner:
        adapter = WISDMAdapter()
        # Inject codebook into Stage 2
        stage2.codebook_str = adapter.get_codebook_str()
        # Materialise derived columns before agent execution
        df, provenance = adapter.get_derived_features(df)
    """

    def get_codebook(self, df: pd.DataFrame) -> dict:
        """
        Return the full ACTIVITY_CODEBOOK dict.

        The returned dict is suitable for injection into Stage 2 grounding prompts
        and for use in synthesis to translate letter codes into English names.

        Returns:
            dict with key "activity_label" mapping letter codes to English names.
        """
        return ACTIVITY_CODEBOOK

    def get_codebook_str(self) -> str:
        """
        Return a human-readable string representation of the activity codebook
        suitable for injection into SCHEMA_GROUNDING_PROMPT's {codebook} placeholder.

        Format:
            activity_label codes:
              A = Walking
              B = Jogging
              ...

        Returns:
            Multi-line string.
        """
        lines = ["activity_label codes:"]
        for code, name in ACTIVITY_CODEBOOK["activity_label"].items():
            lines.append(f"  {code} = {name}")
        return "\n".join(lines)

    def get_derived_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """
        Add derived columns to the DataFrame.

        Derived columns added:
          magnitude   = (x**2 + y**2 + z**2) ** 0.5
                        Scalar acceleration intensity. Critical for Q2, Q3, Q5, Q6.
          activity_name = ACTIVITY_CODEBOOK["activity_label"].get(label, label)
                        English human-readable label. Used in synthesis responses.

        Args:
            df: Raw WISDM DataFrame with columns [subject_id, activity_label,
                timestamp, x, y, z].

        Returns:
            (enriched_df, provenance_map) where:
              enriched_df  — df with magnitude and activity_name columns added in-place.
              provenance_map — dict describing each derived column's formula:
                {
                  "magnitude":     "sqrt(x^2 + y^2 + z^2)",
                  "activity_name": "ACTIVITY_CODEBOOK lookup on activity_label",
                }

        Notes:
          - Do not modify the original df; work on a copy: df = df.copy()
          - activity_label values may have leading/trailing spaces — strip them before lookup.
          - Unknown codes map to themselves (fallback: label as-is).
        """
        df = df.copy()
        df["magnitude"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5
        codebook = ACTIVITY_CODEBOOK["activity_label"]
        labels = df["activity_label"]
        if pd.api.types.is_string_dtype(labels) or labels.dtype == object:
            labels = labels.astype(str).str.strip()
        df["activity_name"] = labels.map(lambda c: codebook.get(c, c))
        provenance = {
            "magnitude": "sqrt(x^2 + y^2 + z^2)",
            "activity_name": "ACTIVITY_CODEBOOK lookup on activity_label",
        }
        return df, provenance
