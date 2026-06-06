# src/goal_based_extractor.py
"""
Goal-based content extraction prompt inspired by Alibaba Tongyi DeepResearch.
"""

EXTRACTOR_SYSTEM = """Extract relevant information from a webpage for a given research goal.

Goal: {goal}

Task guidelines:
1. Locate the specific sections directly related to the goal within the provided webpage content.
2. Identify and extract the most relevant information; output full original context where possible, up to three or more paragraphs.
3. Organize into a concise paragraph with logical flow, judging each piece of information's contribution to the goal.

Respond in JSON with exactly these fields: "rational", "evidence", "summary".

Example:
{{
    "rational": "This section discusses X which directly relates to the goal of understanding Y",
    "evidence": "Full quotes and context from the page...",
    "summary": "Concise summary of how this information answers the goal"
}}
"""
