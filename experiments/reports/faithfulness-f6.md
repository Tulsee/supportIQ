# Distortion-aware faithfulness checks: research feature F6

Research feature F6 of the [research plan](../../docs/research-plan.md#f6-distortion-aware-faithfulness-checking).
Run on 26 November 2026 with `uv run python -m app.cli evaluate-faithfulness --name f6`
(from `backend/`). Generated output: [`results/20261126-193937-f6.md`](../results/20261126-193937-f6.md)
and `.json`; the judge's replies are cached in `results/faithfulness-replies-qwen3-5-4b.jsonl`.

## Why

In the [knowledge-gaps study](knowledge-gaps-g1-g3.md#after-the-fixes-runs-2-and-3), three
distorted statements were published although every check passed, the NLI model
included. For example, "the **buyer** should remove the device" was written where the
reply addressed the seller. The question here is which checks can tell a faithful
restatement from a distorted one without rejecting faithful ones.

## Summary

- **A language model as judge catches 92% of the test distortions and flags none of
  the faithful statements.** The judge is the same local `qwen3.5:4b`, with one
  question per statement (58/63 caught, CI 83-97%; 0/30 faithful flagged, CI 0-11%).
  The NLI check that gap drafts use today (contradiction ≥ 0.5) catches 60% (38/63),
  and the rules 59% (37/63).
- **The judge catches what NLI misses.** Of the 25 test distortions the NLI check
  lets through, the judge catches 20: all 9 scope changes, 7 of 9 role changes and 4
  of 6 modality changes. The rules catch 10 of the 25.
- **Hypothesis H7 is supported, for the judge:** it catches 20 of the 24 role, scope
  and modality distortions that NLI scores as neutral or entailed (83%). The rules
  alone catch 10.
- **Hypothesis H8 is supported for the judge on its point estimate:** 0 of 30 faithful
  statements flagged. The interval (0-11%) is too wide to show the 5% bound. The rules
  flag 7% (2/30) and fail it.
- **Requiring entailment doesn't work:** no threshold keeps faithful rejections at or
  below 5% on dev. **The NLI threshold chosen on dev doesn't transfer:** 0.05 flagged
  no faithful dev statement but 4 of 30 on test.
- **The judge's misses are subtle, and the modality rule covers two of them.** The judge
  missed a bound turned around ("at least 20" for "up to 20"), two role changes and
  two added hedges ("may send", "might need"). The modality rule catches both hedges.
  With it, the judge catches 60 of 63 test distortions and still flags no faithful
  statement. **That combination was chosen after seeing the test results:** it is the
  configuration to try next, not a result.
- **Cost:** the judge takes a median of 18 s per statement on the laptop CPU; the rules
  and NLI take milliseconds. Gap drafts are written in a background job with at most
  10 statements, so the judge would add about 3 minutes to a draft. That is too slow
  to check live answers on a CPU.

## Setup

| | |
| --- | --- |
| Dataset | D5, [`datasets/distortions/pairs_v1.jsonl`](../../datasets/README.md#distortion-pairs-d5-research-feature-f6): 200 pairs from 51 source sentences (25 from agents' replies, 26 from the Quillon documents); 65 faithful, 135 distorted (role 23, scope 40, polarity 27, modality 17, quantity 28). Dev 107, test 93, split by source |
| Rules | `ai_engine/faithfulness/rules.py`: quantities with their units, names, negation by clause, modality, named parties |
| NLI | `cross-encoder/nli-deberta-v3-xsmall`, the model the answer and gap checks use, one pair at a time |
| Judge | `qwen3.5:4b` through Ollama, temperature 0, seed 42, JSON-schema output; the prompt lists the five kinds of change (`ai_engine/faithfulness/judge.py`); laptop CPU |
| Thresholds | Chosen on dev: the most distortions caught with at most 5% of faithful statements flagged; ties go to the stricter threshold |
| Calls | 200 judge calls, no errors or unusable replies; median 18.2 s, mean 19.1 s, max 42.5 s |

## Results (test split)

| Check | Caught | Faithful flagged | Role | Scope | Polarity | Modality | Quantity |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NLI contradiction ≥ 0.5 (gap drafts today) | 38/63 (60%) | 1/30 | 3/12 | 11/20 | 11/11 | 2/8 | 11/12 |
| NLI contradiction ≥ 0.05 (chosen on dev) | 44/63 (70%) | 4/30 | 5/12 | 14/20 | 11/11 | 3/8 | 11/12 |
| Rules | 37/63 (59%) | 2/30 | 4/12 | 5/20 | 10/11 | 7/8 | 11/12 |
| Rules + NLI ≥ 0.5 | 48/63 (76%) | 3/30 | 5/12 | 13/20 | 11/11 | 8/8 | 11/12 |
| **Judge** | **58/63 (92%)** | **0/30** | 10/12 | 20/20 | 11/11 | 6/8 | 11/12 |
| NLI ≥ 0.5 + judge | 58/63 (92%) | 1/30 | 10/12 | 20/20 | 11/11 | 6/8 | 11/12 |
| Rules + judge | 60/63 (95%) | 2/30 | 10/12 | 20/20 | 11/11 | 8/8 | 11/12 |
| *Judge + modality rule (chosen after the fact)* | *60/63 (95%)* | *0/30* | 10/12 | 20/20 | 11/11 | 8/8 | 11/12 |

Intervals for the main rows: judge 83-97% caught, 0-11% flagged; NLI ≥ 0.5 48-71% caught;
rules 46-70% caught, 2-21% flagged. Requiring entailment has no row: on dev, no
threshold kept faithful rejections at or below 5%. Each rule's own numbers, and the dev
split, are in the generated output.

**Each check has its own strengths:**

- **NLI** catches every polarity change and most quantity changes: an inversion is what
  a small NLI model sees well. It misses most role and modality changes.
- **The rules** catch quantity and modality changes (11/12 and 7/8). They catch little
  else: a role or scope change that keeps every name and number is invisible to them.
- **The judge** catches every scope change and most role changes. Its "distorted"
  verdict is reliable, but the kind of change it names isn't (right for 6 of the 58 it
  caught): use the verdict only.

### The distortions from the knowledge-gaps study

| Pair | Split | Rules | NLI ≥ 0.5 | Judge |
| --- | --- | --- | --- | --- |
| "Before selling … **the buyer** should tap Remove device" | test | no | no (0.00) | **yes** |
| "**To transfer** a Quillon Secure subscription, cancel your own" | test | yes (an obligation added) | no (0.01) | yes |
| "The Q3 does not support IPv6" (from "I'll get back to you") | dev | yes | yes (0.99) | yes |
| "**Secure Plus** subscriptions cannot be transferred" (for Quillon Secure) | dev | no | no (0.02) | **no** |

The plan name change is missed by every check. The customer's own message on that
ticket mentions Secure Plus, so the name looks grounded to the rules. To the judge,
naming the customer's plan looks like naming what "it" refers to.

### What the judge missed (test)

| Pair | Type | Caught by |
| --- | --- | --- |
| "The Q3 supports **at least** 20 port forwarding rules" (up to 20) | quantity | none |
| "When the card is full, **you have to delete** the oldest recordings" (the camera overwrites them) | role | none |
| "With error E-4012, **support will contact you**" (you contact support) | role | none |
| "If the claim is approved, we **may** send a replacement" | modality | the modality rule |
| "After a factory reset, you **might** need to set up the router again" | modality | the modality rule |

**Faithful statements flagged on test:**

- **Rules, 2:**
  - "After the first 2 hours, or once it has shipped, contact the carrier" adds a number
    from the surrounding reply. The quantity rule requires numbers to come from the
    quote, as the gap drafter does. Whether this is faithful is a labelling choice.
  - "costs nothing": the polarity rule counts "nothing" as a negation, and the quote's
    "is free" has none.
- **NLI at 0.5, 1:** the Remove-device steps restated in parentheses (contradiction
  0.52).

## How the rules were developed

The first version of the rules flagged 10 of 35 faithful dev statements. Its test
results were also seen once, in the first offline run. The fixes were made by
looking at the dev split only, and each fixes a general cause, not a pair:

- negated obligations ("don't have to");
- the pronoun "one" read as a number;
- a measure reworded ("1080p video" for "1080p recording");
- negations compared per clause, against the part of the quote a statement restates;
- "we" standing for the organization's name;
- "p.m. Pacific" read as the start of a sentence.

After the fixes the rules flagged none of the dev faithful statements, at the cost of
some catches that had come from the same over-broad rules. The rules were then frozen,
along with the combinations to report, before the judge ran. The development runs are
kept beside the final one: `results/*-f6-offline.*` (the first version) and
`*-f6-offline-rules-v2` to `-v4`.

## Threats to validity

- **The same author wrote the data, the rules and the judge's prompt.** That author was
  an AI coding assistant (Claude), in one session; the pairs were written before the
  checks. The judge's prompt lists the same five kinds of change the dataset uses.
  Distortions written by a language model may also be easier for a language-model judge
  to spot than distortions a model makes while drafting. **The G1-G3 rerun (next
  steps) is the real test:** there, the distortions come from the drafting model, not
  from this dataset.
- **No second annotator.** The labels haven't been checked by another person. Two pairs
  are borderline: the faithful "after the first 2 hours" (it adds context), and "may
  send" (a hedge some readers would accept).
- **Small numbers.** 63 test distortions, 30 test faithful statements; 8-20 per type.
  The judge's 0/30 has an upper bound of 11%.
- **The rules were tuned on dev, and their first version's test results were seen
  once.** The NLI threshold, by contrast, shows how unstable a choice made on 35 dev
  faithful statements is (0% flagged on dev, 13% on test).
- **One judge model, one run,** at temperature 0 with a fixed seed. A larger model may
  do better, and another prompt worse. Neither was measured.
- **Synthetic, short sources.** One or two sentences each. Real agent replies are
  longer and mix several issues.

## What this means for the product

- **For gap drafts, the judge is worth its cost.** Drafts run in a background job, and
  the judge would add about 3 minutes to a draft. In this test it caught the role and
  scope changes that reached published articles in G1-G3, without rejecting faithful
  statements.
- **Keep the NLI check in front of it:** at milliseconds per statement, it catches every
  outright inversion.
- **Add the modality rule** if the G1-G3 rerun confirms it: it caught the hedges the
  judge accepted, and flagged nothing faithful on either split.
- **The other rules are too noisy to reject statements on their own.** They could be
  shown to the admin as hints.
- **For live answers, the judge is too slow on a CPU:** 18 seconds per claim, on top of
  generation. The NLI check stays there.

## Next steps

1. **Rerun G1-G3 with the judge and the modality rule in the gap drafter,** and count
   the distortions that reach published articles (three in run 3). That is the
   in-system test this evaluation can't give.
2. **Have a second person label D5,** and report agreement.
3. **Add distortions made by models,** not by hand: drafts collected from G1-G3 runs,
   labelled by people.
4. **Try a larger judge** (or Claude) and a prompt without the list of kinds, to see how
   much the list helps.
