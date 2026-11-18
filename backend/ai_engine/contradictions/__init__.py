"""Knowledge-base contradiction scanning (Module 13): find passages in different
documents that make conflicting claims about the same thing.

Three pure steps; the service gathers the inputs and records the results:

1. ``candidates``: pairs of similar passages from different documents (nearest
   neighbours in the vector index), so the model sees only pairs worth checking.
2. ``judge``: one model call per pair, JSON constrained to a schema: is there a
   conflict, quoted from each passage, with an explanation.
3. ``judge.verify`` and ``judge.decide``: a claimed conflict is reported only when
   both quotes are found in their passages (the model can't invent a conflict) and
   the model's confidence reaches the policy's minimum.
"""
