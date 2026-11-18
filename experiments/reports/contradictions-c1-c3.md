# Contradiction scanner: experiments C1-C3

Module 13 of the [implementation plan](../../IMPLEMENTATION_PLAN.MD#157-module-13---knowledge-base-quality-loop);
the feature is described in [docs/features/contradiction-scanner.md](../../docs/features/contradiction-scanner.md).
Run on 18 November 2026 with `uv run python -m app.cli evaluate-contradictions --name c1-c3`.
Generated output: [`results/20261118-080339-contradictions-c1-c3.md`](../results/20261118-080339-contradictions-c1-c3.md)
and `.json`. The model replies are cached in `results/contradiction-replies-qwen3-5-4b.jsonl`.

## Summary

- **Recall: all six planted contradictions were found** (6/6; 95% CI 61-100%), each with
  quotes that are the planted sentences. With so few positives the interval is wide.
- **Precision against the planted labels: 46%** (6 of 13 flagged pairs; CI 23-71%) with
  the policy chosen before the run. One of the seven other pairs is a real conflict
  nobody planted. Checked by hand, **7 of the 13 pairs are real conflicts (54%)**.
- **Asking again with the passages swapped, and keeping only pairs flagged both times,
  removed half the false alarms and no real conflict:** 6 of 10 against the planted
  labels (60%), **7 of 10 checked by hand (70%)**, still 6/6 recall. It costs 13 more
  model calls (13%). It is now the default; it was chosen after seeing these results
  (see [Threats to validity](#threats-to-validity)).
- **Neither the confidence floor nor the quote check removed anything here.** The model
  reported high confidence for every conflict, and every quote it gave was copied
  correctly. The quote check stays as a safeguard against invented or misquoted
  sentences (covered by unit tests), but this run gives no evidence that it helps.
- **The neighbour search is cheap and found every planted pair:** 97 of 902
  cross-document pairs go to the model (11%), a first scan of about 35 minutes on a
  laptop CPU instead of about 5 hours.

## Setup

| | |
| --- | --- |
| Knowledge base | The 10 Quillon documents (synthetic; [datasets/README.md](../../datasets/README.md)), 53 chunks, 45 of at least 15 tokens (the others are titles on their own), 902 cross-document pairs |
| Gold labels | The 6 `conflicting` questions of `quillon_support_v1.jsonl` (q039-q044): each names two documents and the conflicting sentence in each. A pair is planted when its passages contain both sentences |
| Embeddings | `BAAI/bge-small-en-v1.5`, the vectors already in Qdrant |
| Model | `qwen3.5:4b` through Ollama, temperature 0, seed 42, JSON schema output; laptop CPU |
| Candidates (defaults) | 3 nearest passages in other documents per passage, cosine ≥ 0.5 |
| Calls | Every candidate pair in both orders: 194 calls, no errors, no unusable replies; mean 19.0 s, median 17.0 s, max 55 s |
| Code | commit `d9bd215` plus the uncommitted Module 13 changes |

The labels come from a dataset written before the scanner existed, for a different
purpose (questions the assistant should escalate). No setting was tuned on them, with
one exception: the both-orders default, adopted after this run.

## C1: candidate pairs

The share of planted pairs among the candidates, by neighbours per passage and
similarity floor (no model calls):

| Neighbours | ≥ 0.5 | ≥ 0.6 | ≥ 0.7 | ≥ 0.8 |
| --- | --- | --- | --- | --- |
| 1 | 6/6, 36 pairs | 6/6, 34 | 5/6, 19 | 4/6, 7 |
| 2 | 6/6, 69 | 6/6, 65 | 5/6, 30 | 4/6, 9 |
| **3 (default)** | **6/6, 97** | 6/6, 90 | 5/6, 35 | 4/6, 9 |
| 5 | 6/6, 154 | 6/6, 126 | 5/6, 41 | 4/6, 9 |
| 10 | 6/6, 291 | 6/6, 177 | 5/6, 42 | 4/6, 9 |

Similarity of each planted pair: q039 0.94 (return window), q042 0.89 (guest network),
q040 0.88 (refund time), q043 0.82 (update window), q041 0.76 (phone hours), q044 0.63
(Secure Plus cloud history).

Every planted pair is the nearest cross-document neighbour of at least one of its
passages (one neighbour suffices), so on this knowledge base the number of neighbours
only changes the cost: 36 pairs would have been enough. The weak spot is q044. The camera guide's "Recording and storage"
and the plans page's "Plans and prices" share only the fact in conflict, so they are
only 0.63 similar; a floor of 0.7 misses it. A conflict buried in a passage about
something else will always be the hardest to pair. The default floor, 0.5, is the one
triage uses. A quick check before the run (the same sweep for 3 and 5 neighbours) had
shown no difference in recall anywhere from 0 to 0.6, so this table didn't decide it, but
it wasn't chosen blind either.

## C2: the judge

The same 194 replies, scored under five decision policies:

| Policy | Flagged | Precision, planted labels | Precision, checked by hand | Recall (planted) |
| --- | --- | --- | --- | --- |
| J0: the model's verdict only | 13 | 46% (6/13; 23-71%) | 54% (7/13; 29-77%) | 100% (6/6; 61-100%) |
| J1: + confidence at least medium | 13 | same | same | same |
| J2: + quotes found in both passages (the policy set before the run) | 13 | same | same | same |
| **J3: J2, and both orders agree (now the default)** | **10** | **60% (6/10; 31-83%)** | **70% (7/10; 40-89%)** | **100% (6/6)** |
| J4: quotes found, any confidence | 13 | same as J0 | same | same |

Intervals are Wilson 95%. For every planted conflict found, the quoted sentences are the
planted ones (6/6), so a finding points the reviewer at the right lines, not just the
right pair.

Verdicts on the first order: 13 conflict, 2 consistent, 82 unrelated. The two orders
agreed on conflict or not for 94 of 97 pairs; all three disagreements were false
alarms in the first order.

### Every flagged pair, checked by hand

| Pair (similarity) | What the model quoted | Label | Hand check | Kept by J3 |
| --- | --- | --- | --- | --- |
| FAQ "Can I return" / Returns "Returning a product" (0.94) | any product within 60 days vs unopened within 30 days | q039 | Real | Yes |
| Setup guide "Guest network" / FAQ guests (0.89) | 32 vs 16 devices | q042 | Real | Yes |
| Returns "Refunds" / FAQ "How long do refunds take?" (0.88) | 5 to 7 business days vs up to 14 | q040 | Real | Yes |
| Release notes "How updates are installed" / Setup guide "Automatic updates" (0.82) | 2-4 a.m. vs 3-5 a.m. | q043 | Real | Yes |
| FAQ "When can I call" / Contacting support (0.76) | Mon-Fri 9-5 vs Mon-Sat 7-9 | q041 | Real | Yes |
| Camera guide "Recording and storage" / Secure plans "Plans and prices" (0.63) | 14 vs 30 days of cloud history | q044 | Real | Yes |
| FAQ "Can I return" / Returns "Return shipping" (0.62) | "full refund, no questions asked" vs "the $8.95 cost of the prepaid label is deducted from your refund" | none | **Real, unplanted**: the stale FAQ promises a full refund the policy doesn't give | Yes |
| Secure plans "Cancelling" / FAQ "Can I return" (0.84) | annual plan refunded within 14 days vs any product within 60 days | none | False: a subscription and a product (the returns policy excludes subscriptions) | Yes |
| Release notes 4.1.3 / Setup guide "Status light" (0.73) | "fixes a problem where the light stayed amber" vs "solid amber means no internet" | none | False: a bug fix, not a different claim | Yes |
| Release notes 4.2.0 / Error codes (0.68) | "fixes false E-310 alerts" vs what E-310 means | none | False: a bug fix | Yes |
| Returns "Subscriptions" / Secure plans "Cancelling" (0.78) | "not covered by this policy, see the plans page" vs the plans page's refund rule | none | False: one refers to the other | No |
| Returns "Refunds" / FAQ "Can I return" (0.73) | refunds in 5 to 7 business days vs return within 60 days | none | False: two different facts | No |
| Warranty "Making a claim" / Returns "Return shipping" (0.68) | prepaid label for the faulty device vs $8.95 deducted for other returns | none | False: return shipping is free for defective products | No |

The false alarms fall into two patterns:

1. **Same words, different subject** (3): refunds for a subscription against refunds for
   a product, refund timing against the return window, warranty returns against other
   returns. The prompt already says claims about different products or situations are
   not conflicts; the both-orders check removed two of these three.
2. **A release note describing a fixed bug** (2): "fixes a problem where X" read as a
   claim that contradicts the documented behaviour X. Both survive J3, because the
   model makes the same mistake in either order. A prompt rule ("a fix for a bug is not
   a claim about current behaviour") is the obvious next step. It would have to be
   checked on new data, not on this run.

## C3: cost

| | Model calls | Time at the measured mean (19.0 s) |
| --- | --- | --- |
| Every cross-document pair, one order | 902 | about 4.8 hours |
| Candidates, one order (J2) | 97 | about 31 minutes |
| **Candidates, second order for flagged pairs (J3, default)** | **110** | **about 35 minutes** |

Later scans judge only pairs whose text is new or changed, and a scan stops at 40 pairs
(the next continues), so the model is never occupied for hours at a stretch.

## Threats to validity

- **Six positives.** The recall interval runs from 61% to 100%. "Found all six" is
  consistent with a true recall well below that on other knowledge bases.
- **Planted by the same people who built the system.** The conflicts are clean numeric
  disagreements in short sections (30 vs 60 days, 16 vs 32 devices). Real knowledge
  bases have conflicts in conditions and exceptions, spread across long passages, which
  will be harder both to pair and to judge.
- **The both-orders default was chosen after seeing this run,** on the only labelled
  data there is. Its effect (3 false alarms removed, nothing real lost) is measured on
  the same pairs that motivated it. The extra cost is small and the mechanism (position
  bias) is well known, but the size of the gain needs confirming on new data.
- **The hand check was done by the person who built the scanner,** with the documents
  open. The judgements are written out in the table above so they can be disputed. The
  borderline case is the subscription refund against the product return, judged a false
  alarm because the returns policy explicitly excludes subscriptions.
- **One model, one run.** `qwen3.5:4b` at temperature 0 with a fixed seed. A larger model
  may make fewer of the "bug fix" mistakes; this wasn't measured.
- **Unflagged pairs weren't all read.** A real conflict the model called unrelated, outside
  the six planted, would not be noticed. Its 82 "unrelated" verdicts are mostly pairs
  that share words but not subjects; they weren't checked one by one.

## What this means for the product

At the default settings, an admin of a knowledge base like this one gets 10 findings,
7 of them real and every one shown with the two conflicting sentences highlighted, so a
false alarm takes seconds to dismiss (and stays dismissed until the text changes). For
the escalations the [answer evaluation](quillon-v1-evaluation.md) attributed to
contradictory documents, that's the useful trade: every planted conflict surfaced, with
little review effort.
