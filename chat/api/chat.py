"""Vercel serverless handler for /api/chat.

Lightweight multi-domain conversational interface backed by Groq.
Handles domain detection (keyword + zero-shot LLM fallback) and carries
full conversation history from the client on each request (stateless).

Domains supported: ECG, IMU/HAR, Bus telemetry.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

from groq import Groq

# ── Domain detection ────────────────────────────────────────

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "ecg": [
        "ecg", "heart", "cardiac", "heartbeat", "rhythm", "arrhythmia",
        "beat", "annotation", "st segment", "wfdb", "recording", "pulse",
        "p-wave", "qrs", "t-wave", "r-peak", "mitdb", "bradycardia", "tachycardia",
    ],
    "imu": [
        "imu", "accelerometer", "activity", "walking", "jogging", "running",
        "wisdm", "gyroscope", "motion", "movement", "exercise", "accel",
        "downstairs", "upstairs", "sitting", "standing", "har",
        "human activity", "activity recognition",
    ],
    "bus": [
        "bus", "ride", "pothole", "bump", "road", "passenger",
        "driving", "vehicle", "comfort", "maintenance", "bumpy", "trip",
        "driver", "route", "dwell", "aggressive",
    ],
}

DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "ecg": (
        "ECG domain — MIT-BIH Arrhythmia Database (PhysioNet).  Contains multi-lead "
        "ECG recordings with beat-level annotations (Normal, LBBB, RBBB, PVC, etc.), "
        "signal quality metadata, and rhythm labels.  Useful for cardiac rhythm "
        "analysis, arrhythmia classification, and beat-interval statistics."
    ),
    "imu": (
        "IMU / Human Activity Recognition domain — WISDM dataset.  Contains tri-axial "
        "accelerometer readings (x, y, z) labeled with activities: Walking, Jogging, "
        "Upstairs, Downstairs, Sitting, Standing.  Useful for activity distribution, "
        "motion intensity analysis, and sensor feature exploration."
    ),
    "bus": (
        "Bus telemetry domain — campus bus driving behavior dataset.  Contains GPS "
        "coordinates, timestamps, tri-axial acceleration statistics (mean, std, "
        "percentiles), instability scores, behavior labels (Calm, Moderate, Slightly "
        "Unstable, Aggressive, Very Aggressive), and cluster IDs.  Useful for "
        "driving pattern analysis, route efficiency, and safety assessment."
    ),
}


def _detect_domain_keywords(query: str) -> str | None:
    q = query.lower()
    scores = {d: sum(1 for kw in kws if kw in q) for d, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _classify_domain_llm(
    query: str,
    client: Groq,
    model: str,
    history: list[dict] | None = None,
) -> str | None:
    """Zero-shot LLM domain classification with conversation history context."""
    system = (
        "Classify the user query as exactly one of: ecg, imu, bus, unknown.\n"
        "- ecg: heart rate, cardiac signals, ECG, arrhythmia, beats, wfdb\n"
        "- imu: activities, accelerometer data, walking/jogging/running, WISDM, HAR\n"
        "- bus: bus rides, road quality, driving comfort, route/pothole analysis\n"
        "- unknown: general questions not clearly about a specific dataset\n"
        "If the current query is ambiguous, use the conversation history for context.\n"
        "Reply with exactly one word."
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        for msg in history[-6:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append({
                    "role": msg["role"],
                    "content": str(msg.get("content", ""))[:500],
                })
    messages.append({"role": "user", "content": query})
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, max_tokens=5, temperature=0,
        )
        result = resp.choices[0].message.content.strip().lower()
        return result if result in ("ecg", "imu", "bus") else None
    except Exception:
        return None


# ── System prompt ───────────────────────────────────────────

SYSTEM_PROMPT = """\
You are Flash-Fusion, a multi-domain IoT data exploration assistant built at \
Georgia Tech. You help users understand and explore three datasets:

1. **ECG** — MIT-BIH Arrhythmia Database (PhysioNet): multi-lead ECG recordings \
with beat-level annotations (Normal, LBBB, RBBB, PVC, etc.), rhythm labels, and \
signal quality metadata.

2. **IMU / HAR** — WISDM Human Activity Recognition: tri-axial accelerometer \
readings labeled with activities (Walking, Jogging, Upstairs, Downstairs, Sitting, \
Standing).

3. **Bus Telemetry** — Campus bus driving behavior: GPS coordinates, timestamps, \
tri-axial acceleration statistics, instability scores, behavior labels (Calm → \
Very Aggressive), and spatial cluster IDs.

Guidelines:
- Answer questions conversationally and accurately.
- When the user's question maps to a specific domain, provide domain-relevant \
  context and insights.
- If a concept doesn't exist directly in the data, explain what proxy metrics \
  could approximate it (e.g., "road roughness" → acceleration variance).
- Be concise but informative. Use numbers and specifics when possible.
- If the domain is ambiguous, ask the user to clarify.
- Never fabricate data values — state what the dataset contains and what kinds \
  of analyses are possible.\
"""


# ── Vercel handler ──────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"error": "Invalid JSON"})
            return

        message = (req.get("message") or "").strip()
        if not message:
            self._json_response(400, {"error": "Empty message"})
            return

        model = req.get("model", "llama-3.3-70b-versatile")
        history: list[dict] = req.get("history", [])
        explicit_domain: str | None = req.get("domain")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            self._json_response(500, {"error": "GROQ_API_KEY not configured"})
            return

        client = Groq(api_key=api_key)

        # Domain detection: explicit > keywords > LLM zero-shot (with history)
        domain = explicit_domain
        if not domain:
            domain = _detect_domain_keywords(message)
        if not domain:
            domain = _classify_domain_llm(message, client, model, history)

        # Build messages for Groq
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject domain context if detected
        if domain and domain in DOMAIN_DESCRIPTIONS:
            messages.append({
                "role": "system",
                "content": f"The user's question is about the {domain.upper()} domain. "
                           f"{DOMAIN_DESCRIPTIONS[domain]}",
            })

        # Append conversation history (capped to avoid token overflow)
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": message})

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                max_tokens=1024,
            )
            reply = resp.choices[0].message.content or ""
            usage = resp.usage
            self._json_response(200, {
                "reply": reply,
                "domain": domain,
                "usage": {
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                } if usage else None,
            })
        except Exception as exc:
            self._json_response(502, {"error": f"LLM error: {exc}"})

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
