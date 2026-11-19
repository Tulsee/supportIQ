"""Knowledge gaps to articles (Module 13, second part): questions the assistant could
not answer, grouped, and drafted into an article from the agents' replies on resolved
tickets that did answer them.

Pure steps; the service gathers the inputs and records the results:

1. ``cluster``: questions grouped by meaning (embeddings, average linkage), and new
   questions joined to existing gaps, so a gap keeps its identity (and an admin's
   decision about it) as more questions arrive.
2. ``draft``: one model call per gap, JSON constrained to a schema: an article whose
   every statement quotes the agent reply it comes from.
3. ``draft.verify``: a statement is kept only when its quote is found in that ticket's
   agent replies and every number it states is in the quote; statements that name the
   customer, an email address, a phone number or an order or ticket reference are left
   out. What is left is the draft an admin edits and publishes.
"""
