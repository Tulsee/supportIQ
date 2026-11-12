"""Evidence assessment (Module 5): is the retrieved evidence good enough to answer,
and is the generated answer supported by it?

Signals (``signals``), claim checks with a small NLI model (``nli``, ``grounding``),
and the decision policy that turns them into answer / clarify / escalate / abstain
(``policy``). Thresholds are tuned on the dev split of the evaluation dataset.
"""
