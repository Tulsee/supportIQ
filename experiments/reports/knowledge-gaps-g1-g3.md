# Knowledge gaps to articles: experiments G1-G3

Module 13 of the [implementation plan](../../IMPLEMENTATION_PLAN.MD#157-module-13---knowledge-base-quality-loop);
the feature is described in [docs/features/knowledge-gaps.md](../../docs/features/knowledge-gaps.md).
Run on 26 November 2026 with `uv run python -m app.cli evaluate-gaps --name g1-g3 --regression`.
It was then run twice more with fixes, as `--name g1-g3-fixes` and `--name g1-g3-nli`.
Generated output: [`results/20261126-113120-g1-g3.md`](../results/20261126-113120-g1-g3.md)
and `.json`, with the later runs beside it. The model replies of all three runs are
cached in `results/gap-replies-qwen3-5-4b.jsonl`.

Everything up to [Faults to fix](#faults-to-fix) is about this first run, made with the
code as committed. Fixing those faults took two more runs, reported in
[After the fixes](#after-the-fixes-runs-2-and-3). With the fixes, 14 of 20 held-out
questions are correctly answerable instead of 10. The second run showed that a prompt
change can make the model invent a fact, which led to a fourth check.

## Summary

- **Publishing the drafts unedited made 10 of 20 held-out paraphrases correctly
  answerable, up from 0** (checked by hand; 50%, CI 30-70%). Exact McNemar: 10 questions
  correct only after, none only before, p = 0.002. The automatic scoring gives one
  question as correct before (p = 0.012), but that is a scoring false positive (see
  [G3](#g3-the-effect-of-publishing)). 12 of the 20 were answered after publishing, every
  one citing a new article, and 2 of those answers were incomplete.
- **Nothing private and nothing outdated reached an article.** The planted outdated
  claim (a 60-day return window, against the policy's 30 days) was written into 3 drafts.
  The conflict check removed it each time. No statement named a customer, so the privacy
  check was never needed in this run.
- **Grouping: 0.75 was chosen on the dev topics** (dev pair F1 0.77). It is also the best
  threshold on the test topics (pair F1 0.71). But grouping all 30 questions together, as
  the product does, gives a pair precision of only 0.48. Router questions from different
  topics merge: two of the 12 gaps hold questions from 3 and 4 topics. The shipped
  defaults were the provisional 0.8 during this run; they are now 0.75.
- **8 of the 9 answerable topics that led a gap got a draft, and neither decoy got an
  article of its own.** (The tenth answerable topic, port forwarding, never led a gap: its
  questions went into the router gaps.) The CEO decoy found no source ticket. The other
  decoy is a ticket whose agent only promised to find out about IPv6. Its questions were grouped into the two router
  gaps, and "I'll get back to you" became a statement in both published articles.
- **Three faults explain 7 of the 10 held-out misses** (the answering model accounts for
  the other 3):
  1. **A bug in the number check (4 misses).** It counts the digit in a product name, so
     a statement that says "the Q3" fails when its quote says "it". All 5 of its
     rejections were this. They removed the port forwarding steps, both bridge mode
     statements and the voice assistant fact.
  2. **The conflict check sides with the outdated FAQ (1 miss).** The correct rule,
     "products bought from a retailer go back to the retailer", was left out of 2 of the
     3 drafts that wrote it. The reason: the 2024 FAQ still says any product can be
     returned to Quillon.
  3. **The drafting model misreads negative answers (2 misses).** It judged "we don't
     offer student discounts" as not answering "do you offer a student discount?", so
     that topic got no article.
- **No regression on the benchmark's test split:** decision accuracy was 27/29 before and
  after. Two outcomes changed, and both are now correct. One of them counts as an
  "unwarranted answer" only because its label predates the new article (q050, the
  status page address).

## Setup

| | |
| --- | --- |
| Knowledge base | The 10 Quillon documents (synthetic; [datasets/README.md](../../datasets/README.md)), indexed into their own organization |
| Tickets | 23 resolved tickets ([`datasets/knowledge_gaps/tickets_v1.jsonl`](../../datasets/knowledge_gaps/tickets_v1.jsonl)): 14 answer a gap topic, 2 are decoys (the agent didn't answer), 9 are distractors on topics the documents already cover |
| Topics | 12 (6 dev, 6 test): 34 questions as asked, 22 held-out paraphrases with key facts. 10 topics are answerable; 2 are decoys (g11, the CEO; g12, IPv6) |
| Planted problems | The outdated 60-day return window in `t05`; customers' names, order numbers and a phone number in `t09` and `t12` |
| Embeddings | `BAAI/bge-small-en-v1.5` |
| Model | `qwen3.5:4b` through Ollama, temperature 0, seed 42, JSON-schema output; laptop CPU |
| Settings | The defaults in [the feature doc](../../docs/features/knowledge-gaps.md#settings), except the grouping threshold, which G1 chooses |
| Calls | 184 model requests: 169 replayed from the cache, 15 new. 168 distinct replies, no errors, no unusable replies. One cached reply is missing its model digest (the first call of an earlier session); all the others are the same build (`2a654d98…`) |
| Code | commit `451c443`, with no uncommitted code changes (the run says "dirty" only because of untracked files) |

**Where the cached replies come from.** Most were made by an earlier run that stopped
partway through G3. Replies are keyed by the exact prompt and model, and the model runs
at temperature 0 with a fixed seed. So replaying a reply is equivalent to asking again,
and any prompt that changed would have missed the cache and been asked fresh.

## G0: which questions count as gaps

30 of the 34 asked questions went unanswered (all abstained), so they became the input
to grouping. The assistant answered the other 4, which keeps them out of the gap list.
Checked by hand, only one of the four answers is right:

| Question (topic) | Answer | Hand check |
| --- | --- | --- |
| Is there a website that shows Quillon outages? (g03) | Updates are posted on the Quillon status page | Right: the documents mention the page, though not its address |
| Can the Q3 work behind my provider's router? (g06) | "Yes … because you must plug the Ethernet cable from your modem into the WAN port" | Not supported: the reason given has nothing to do with the question, and the documents don't cover bridge mode |
| What should I do before giving my router to someone else? (g07) | Factory reset, adding that the documents don't say whether it's needed | Partial: it misses removing the device from the account |
| Which Wi-Fi standard does the Q3 support? (g10) | WPA3 encryption | Wrong: it names the encryption, not the Wi-Fi standard |

This finding is about the answer pipeline, not this feature, but it limits the feature.
Gaps are found only where the assistant knows it can't answer. A confident off-target
answer, like the WPA3 one, never becomes a gap.

## G1: grouping

The pair scores count question pairs: a pair is correct when both questions belong to the
same topic. The dev and test columns group each split's questions on their own. "All 30
together" is what the live analysis does.

| Threshold | Dev pair F1 | Test pair P | Test pair R | Test pair F1 | Test B³ F1 | All 30 together: P / R / F1 | Gaps (all 30) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.50 | 0.29 | 0.11 | 1.00 | 0.20 | 0.30 | 0.06 / 1.00 / 0.10 | 1 |
| 0.55 | 0.43 | 0.19 | 0.80 | 0.30 | 0.44 | 0.12 / 0.75 / 0.21 | 4 |
| 0.60 | 0.45 | 0.21 | 0.80 | 0.33 | 0.53 | 0.13 / 0.75 / 0.22 | 5 |
| 0.65 | 0.45 | 0.35 | 0.80 | 0.48 | 0.69 | 0.17 / 0.75 / 0.28 | 6 |
| 0.70 | 0.59 | 0.47 | 0.80 | 0.59 | 0.77 | 0.29 / 0.75 / 0.42 | 8 |
| **0.75 (chosen on dev; now the default)** | **0.77** | **0.86** | **0.60** | **0.71** | **0.84** | **0.48 / 0.67 / 0.56** | **12** |
| 0.80 (the default during run 1) | 0.67 | 1.00 | 0.40 | 0.57 | 0.80 | 0.67 / 0.50 / 0.57 | 17 |
| 0.85 | 0.33 | 1.00 | 0.20 | 0.33 | 0.73 | 0.83 / 0.21 / 0.33 | 24 |
| 0.90 | 0.13 | 1.00 | 0.10 | 0.18 | 0.67 | 1.00 / 0.08 / 0.15 | 28 |
| 0.95 | 0.13 | 1.00 | 0.00 | 0.00 | 0.60 | 1.00 / 0.04 / 0.08 | 29 |

The threshold chosen on dev is also the best on test, so the choice transfers. But the
per-split numbers flatter grouping. Each split holds six topics, while all 30 questions
together put every router question in one pool. At 0.75 that pool forms two mixed gaps:

- **"Can I add my Quillon router to the Apple Home app?"** HomeKit (2 questions), plus
  port forwarding, transferring a subscription and the IPv6 decoy.
- **"Can I open a port on my Q3 router for a game server?"** Port forwarding (2), bridge
  mode (2) and the IPv6 decoy.

The other 10 gaps hold one topic each. Counting both kinds, three topics end up in two
gaps each: HomeKit, selling the router, and the student discount. The merged questions
are all about the router and share its vocabulary ("Q3", "Quillon router", "the Quillon
Home app"). The likely explanation, though it wasn't measured, is that the embedding model
weighs that shared vocabulary more than what is being asked. In the live setting, 0.75
and 0.8 score about the same (F1 0.56 against 0.57), and 0.75 gives an admin 12 gaps to
review instead of 17. Grouping makes no model call, so all three runs grouped
identically.

## G2: drafting

| Measure | Result |
| --- | --- |
| Answerable topics drafted | 8/9 (89%, CI 56-98%) of those that led a gap; the student discount got no draft (see below), and port forwarding led no gap |
| Decoy gaps drafted (must be 0) | 0/1: g11 found no source. g12 didn't form a gap of its own, so this measure missed it |
| Source tickets: recall / precision | 10/10 (CI 72-100%) / 10/22 (45%, CI 27-65%) |
| Statements kept / written | 29 / 43 |
| Left out, by reason | number not in quote 5 (all false, see below), conflict 9 |
| Customers' details in a body (must be 0) | 0 |
| Outdated claim in a body (must be 0) | 0; written in 3 drafts, removed by the conflict check each time |
| Key facts in the body (automatic) | 11/17 (65%, CI 41-83%); every key fact in 5/9 drafts |

Low source precision is partly a result of the merged gaps: a ticket can be the gold
source of another topic in the same gap. The rest are near-misses from the keyword
search, such as the retailer-returns ticket `t05` drafted into the delivery-address gap.

### Every draft, checked by hand

| Gap (topic) | Sources (gold) | Kept / written | Hand check |
| --- | --- | --- | --- |
| Apple Home app (g05, with g01, g07, g12 questions) | t08, t14, t06, t04 (t06) | 4/5 | HomeKit not supported: right. The Alexa fact was removed by the number bug. Also has the selling steps and the subscription rule, which answer other questions in this mixed gap, and the IPv6 "being investigated" non-answer |
| Port for a game server (g01 and g06, with g12) | t14, t07, t01, t02 (t07) | 3/6 | The 20-rule limit and "no restart needed" are right. The port forwarding steps and both bridge mode statements were removed by the number bug, so neither topic's main answer is there. Has the IPv6 non-answer |
| Delivery address (g08) | t09, t05 (t09) | 4/7 | Right (2 hours, then the carrier), plus an off-topic warranty sentence from `t05`. 3 removed for conflict |
| Retailer returns (g04) | t05, t18, t04, t10 (t05) | 1/4 | **Misses the answer.** Only "the warranty still applies" is left. The retailer rule and the 60-day claim were removed for conflict. The automatic score says both key facts are present, because they are just the words "retailer" and "warranty" |
| SD card full (g02) | t03 (t03) | 4/4 | Right |
| Wi-Fi speed (g10) | t11, t12, t07 (t11, t12) | 4/4 | Right; 5400 Mbps is stated twice (once from each ticket) |
| Student discount (g09, two gaps) | t10 (t10) | 0/0 | **Wrong verdict.** `t10` says "We don't offer student, teacher or military discounts", but the model judged that the replies don't answer the question |
| Status page (g03) | t04 (t04) | 3/3 | Right |
| CEO (g11, decoy) | none | 0/0 | Right: no source, no model call |
| Sold my Q3 (g07) | t08, t05 (t08) | 5/8 | Removal steps right. The non-transferable subscription was removed by a false alarm. Three off-topic retailer-return statements were kept |
| Apple HomeKit (g05) | t06, t07, t02, t05 (t06) | 1/2 | HomeKit not supported: right. Alexa removed by the number bug |

### What the checks left out

- **Number not in quote: 5 of 5 wrong.** The check requires every number in a statement
  to appear in its quote, and it reads the "3" of "Q3" as a number. The model writes
  "To enable bridge mode on the Q3, open the app…" from a quote that starts "In the
  Quillon Home app…", and the statement is rejected. No real number was changed in any
  statement in this run, so the check caught nothing real and cost the main answer of two
  topics.
- **Conflict: 9 statements, 7 conflicts flagged over 27 passages checked.** By hand:
  - **The planted 60-day claim (3 statements, in 3 drafts): right.** Each time, the judge
    quoted it against the policy's 30 days.
  - **The retailer rule (4 statements, 2 drafts): a real conflict, but the wrong side
    wins.** The judge quoted the rule against the 2024 FAQ ("any Quillon product within
    60 days, no questions asked"). That FAQ is the outdated document that the
    contradiction scanner already flags. Leaving the statement out follows the rule that a
    person decides which side is right. But the article loses its main answer, and nothing
    tells the admin why. The third draft that wrote the rule (g07) kept it: there the judge
    compared the FAQ with the 60-day statement instead.
  - **The non-transferable subscription (2 statements): a false alarm.** The judge set it
    against the annual plan's 14-day refund, which is about something else. It is the
    "same words, different subject" pattern from
    [C2](contradictions-c1-c3.md#c2-the-judge).

When the judge quotes two statements as one passage, both are left out.

### The IPv6 decoy

The decoy works as intended when it stands alone: a gap whose only ticket says "I've
asked our engineering team… I'll get back to you" should produce nothing. Here its two
questions joined the router gaps, and the model turned that sentence into "Support is
currently investigating IPv6 support on the Q3 router". The statement passes every
check: the quote is real and has no numbers and no names. It isn't a fact about the
product, though. It's a promise made on one ticket that will go stale. The held-out IPv6
question got a clarifying question after publishing ("current availability or future
support plans?"), not an answer. So it isn't counted as an answered decoy, but the
statement is in two published articles.

## G3: the effect of publishing

Every draft was published unedited, as public articles (the worst case: no admin
review). Then the 22 held-out paraphrases were asked again.

| Held-out paraphrases (20 answerable) | Before | After |
| --- | --- | --- |
| Answered | 1/20 (5%) | 12/20 (60%, CI 39-78%) |
| Correct, automatic (every key fact) | 1/20 | 10/20 (50%, CI 30-70%) |
| **Correct, by hand** | **0/20 (CI 0-16%)** | **10/20 (50%, CI 30-70%)** |
| Answers citing a new article | | 12/20 |
| Decoy questions answered (must be 0) | 0/2 | 0/2 |

**The one question scored as correct before is a false positive.** For "Does the Quillon
warranty cover a camera I bought from a retailer?", the answer before publishing said
"the passages do not specify… if the warranty applies to retailers specifically". The key
fact "warranty applies" matches inside that denial. After publishing, the new article was
retrieved (relevance 1.00), but the model replied "not found". So by hand the McNemar
comparison is 10 against 0 (p = 0.002), not 10 against 1 (p = 0.012).

The 12 answers given after publishing, checked by hand: 10 are right. The other 2:

- **"I need to forward port 25565 to my PC. How do I do that?"** It says to add a rule in
  the app but gives no path to the setting. The steps were removed from the article by
  the number bug.
- **"How do I unlink a router I'm selling?"** "Remove it from the app and factory reset"
  is right but leaves out where to tap. The answer also adds the retailer-return rule,
  which reached the answer from the off-topic statements in that gap's article.

Two right answers add true but unneeded details from the documents (return shipping
costs; the Secure Plus trial on a new Q3).

### Why the other 8 weren't answered

| Cause | Questions |
| --- | --- |
| Number bug: the fact was removed from the article | Which voice assistants work with the Q3? · Where is the bridge mode setting? · Do parental controls work in bridge mode? |
| Conflict with the outdated FAQ: the rule was removed from the retailer article | My router came from a third-party seller: can Quillon take it back? (the rule did survive in another article, but retrieval coverage was too low: 0.29) |
| The answering model missed it: "not found" with the article retrieved | Does the warranty cover a camera bought from a retailer? |
| No article (drafting verdict) | Can I get the Q3 cheaper with a student ID? · Are there education discounts? |
| Clarifying question | Is the Q3 a Wi-Fi 6 router? ("6E or just 6?", although the article says 6E) |

Counting the two incomplete answers as misses too, 10 of 20 fail, from these causes:

| Cause | Misses |
| --- | --- |
| Number bug | 4 (with the port forwarding answer) |
| Conflict with the outdated FAQ | 1 |
| Drafting verdict | 2 |
| The answering model | 3 (with the unlink answer) |

### In-product recheck

The product's own recheck asked each gap's questions again after publishing. 18 of 25
were answered (72%, CI 52-86%). The recheck records whether a question was answered and
whether the answer cites the article, not whether it's right; these 25 weren't checked by
hand.

### Regression: the benchmark's test split

| | Before | After |
| --- | --- | --- |
| Decision accuracy | 27/29 (93%) | 27/29 (93%) |
| Correct answers | 15/15 | 16/16 |
| Unwarranted answers | 0/13 | 1/13 |
| Coverage | 15/16 | 16/16 |

Two outcomes changed:

- **q004 (the restocking fee for opened products) went from escalated to answered,
  correctly.** It used to escalate because the outdated FAQ disagrees with the policy.
  The new articles changed which passages were retrieved, and the FAQ passage dropped
  out. This is a side effect, not something the feature set out to do.
- **q050 (the status page's web address) went from abstained to answered.** Its label
  says abstain because the documents mention the page but never its address. The
  published article now gives the address, so the answer is right, and the label is out
  of date. Counted against the old label, it is the one "unwarranted" answer.

## Cost

Times from the cached replies, all measured on the laptop CPU:

| Call | Distinct calls | Mean | Median | Max |
| --- | --- | --- | --- | --- |
| Answer (staff pipeline) | 130 | 28.1 s | 26.6 s | 50.5 s |
| Draft | 11 | 52.7 s | 54.8 s | 89.2 s |
| Conflict judge | 27 | 19.9 s | 16.4 s | 34.3 s |

A full run without the cache would take about 80 minutes. In the product:

- **Finding gaps** makes no model call.
- **Drafting one gap** takes about 2 minutes: one draft call plus up to 3 judge calls.
- **A recheck** takes up to about 5 minutes: 10 questions at about 28 s each.

The generated summary's "median drafting time 59 ms" is replay time, not model time.
Run 2 drafted every gap afresh. Its median, 165 s per gap including the conflict judge
calls, is the real cost. The quote-contradiction check added in run 3 takes about 25 ms
per statement.

## Faults to fix

These were found by looking at this run, so any fix has to be measured in a second run,
reported as a change made after seeing the results. Items 1 and 2 are done (see the
next section); 3 and 4 are still open.

1. **The number check (bug).** Digits that are part of a word, as in Q3, QC2 or 1080p,
   are names, not quantities. Either ignore them, or check them against the whole ticket
   instead of the quote. Standalone numbers (30 days, 5400 Mbps, 20 rules) stay checked
   against the quote.
2. **The drafting prompt.** Two rules are missing:
   - a reply saying that something isn't offered or supported does answer the question;
   - a reply that only promises to find out, or that describes one case's progress,
     isn't a fact for an article.
3. **Conflicts with a document already known to be outdated.** Leaving the statement
   out silently hides the reason from the admin. The draft should show it as a conflict
   and link the open contradiction finding, so the admin can fix the FAQ instead of
   losing the article. This is a design change, and it needs deciding first.
4. **The evaluation itself:**
   - match key facts only outside sentences that deny them;
   - make g04's draft facts specific ("returned to that retailer", "warranty still
     applies");
   - count a decoy as drafted when any article uses its ticket, not only when it forms
     its own gap;
   - relabel q050 for the regression check, since the article answers it.

## After the fixes: runs 2 and 3

All of these changes were made after seeing the first run, and were measured on the
same topics.

| Run | Changes | New model calls | Output |
| --- | --- | --- | --- |
| 1 (`g1-g3`) | none: the code as committed (`451c443`) | 15 (the rest replayed) | [`results/20261126-113120-g1-g3.md`](../results/20261126-113120-g1-g3.md) |
| 2 (`g1-g3-fixes`) | **The number check:** digits inside a name ("Q3", "QC2") are a name, which must appear in the quote or the ticket, not a quantity. **Two prompt rules:** a reply that says something isn't offered or supported answers the question; a reply that only promises to find out, or is about one customer's case, answers nothing. **Grouping defaults** set to 0.75 (this doesn't change the evaluation, because G1 chooses on dev each run) | 103, 61 min | [`results/20261126-133246-g1-g3-fixes.md`](../results/20261126-133246-g1-g3-fixes.md) |
| 3 (`g1-g3-nli`) | **A fourth check:** a statement its own quote contradicts (NLI probability ≥ 0.5) is left out | 17, 11 min | [`results/20261126-135157-g1-g3-nli.md`](../results/20261126-135157-g1-g3-nli.md) |

Run 3 replays run 2's drafting replies, because the prompt didn't change. So it shows
exactly what the fourth check changes: one statement.

| | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| Topics drafted (of the 9 that led a gap) | 8 | 9 | 9 |
| Statements kept / written | 29 / 43 | 45 / 53 | 44 / 53 |
| Left out: number not in quote | 5 (all wrong) | 0 | 0 |
| Left out: conflict with the knowledge base | 9 | 8 | 8 |
| Left out: contradicted by its own quote | | | 1 |
| Key facts in the bodies (automatic) | 11/17 | 15/17 | 15/17 |
| **Invented claims in an article (by hand)** | 0 | **1** | 0 |
| Distorted statements in an article (by hand) | 0 | 3 | 3 |
| Case promises in an article | 2 | 0 | 0 |
| Customers' details or the outdated claim in an article | 0 | 0 | 0 |
| Held-out questions answered | 12/20 | 15/20 | 15/20 |
| **Held-out questions correct, by hand** | 10/20 | 14/20 | **14/20 (70%, CI 48-85%)** |
| **Decoy questions answered (must be 0)** | 0/2 | **1/2** | **0/2** |
| In-product recheck answered | 18/25 | 23/28 | 21/28 |
| Benchmark test split, decision accuracy after | 27/29 | 27/29 | 27/29 |

By hand, run 3 against before publishing is 14 against 0 (exact McNemar p < 0.001). The
recheck asks more questions in runs 2 and 3 (28 against 25), because both
student-discount gaps were drafted and published.

### What the fixes did

- **The number check no longer rejects anything here.** The Alexa fact and the bridge
  mode statements are back in the articles. The two held-out questions it cost in run 1
  are now answered correctly: voice assistants, and parental controls in bridge mode.
- **Gains and losses against run 1, by hand:** 5 questions are newly correct (voice
  assistants, parental controls, education discounts, "Is the Q3 a Wi-Fi 6 router?",
  unlinking the router) and 1 is lost ("Can the buyer of my used Q3 take over my Secure
  plan?": the article was retrieved, but the model said "not found"). That nets out at 10
  to 14.
- **"Isn't offered" now counts as an answer.** Both student-discount gaps were drafted
  ("Quillon does not offer student, teacher, or military discounts"), and one of the two
  held-out questions is answered. The other ("Can I get the Q3 cheaper with a student
  ID?") is a retrieval miss: no passage reached the model (relevance 0.00).
- **The case promise is gone, and in run 2 it came back as a lie.** The model no longer
  copied "I've asked our engineering team about IPv6, I'll get back to you" as a
  promise. Instead, following the new "isn't supported" rule, it wrote **"The Q3 does not
  support IPv6."** Every check passed it: the quote is real, and it has no numbers and no
  names. It was published, and the held-out decoy "Is IPv6 available on the Q3?" was
  answered **"No, the Q3 does not support IPv6"**, with a citation. The two IPv6 recheck
  questions were also answered. That is the kind of failure this project exists to
  prevent: a confident, cited answer that nothing supports.

### The fourth check: the quote must not contradict the statement

The existing checks test that the quote exists, and that numbers and names match. None of
them tests that the statement says what the quote says. The project already has an NLI
model, `cross-encoder/nli-deberta-v3-xsmall`, used by the answer assistant's grounding
check. All 96 statements of runs 1 and 2 were scored, one pair at a time (see below),
with no model calls:

- **Contradiction ≥ 0.5 against the quote flags exactly one statement:** "The Q3 does
  not support IPv6" (0.99). No faithful statement comes close; the highest is 0.30.
- **Requiring entailment instead would reject 32 of the 92 faithful statements.** The
  small model calls many correct paraphrases neutral. For example, "The Q3 router does
  not support Apple HomeKit" scores neutral 0.99 against "The Q3 doesn't support Apple
  HomeKit at the moment".
- **Scoring against the whole agent reply instead of the quote is worse.** It flags 7
  faithful statements as contradicted, such as "To remove a Q3 from your account … tap
  Remove device" (0.99).

So the check counts contradiction against the quote only, at 0.5
(`GAP_QUOTE_CONTRADICTION`). The threshold was chosen on these same statements. In run
3 it removed the IPv6 claim and nothing else. The decoy question then abstained again,
and so did both IPv6 recheck questions, which is why the recheck count fell from 23 to
21.

**The NLI model's scores depend on the batch.** The model is a quantized ONNX build, and
the same pair scores differently depending on what else is in the batch. "To remove a Q3
from your account…" against its quote scores 0.30 alone, 0.45 batched with a short
pair, and 0.55 with a long one, which crosses the threshold. Run 3 scored each draft's
statements as one batch and happened to stay below 0.5. The NLI model now scores one
pair at a time, for this check and for the answer assistant's grounding check. Scored
that way, the check flags the same single statement in every run, so run 3's results
stand without another run. For the answer assistant, replaying the benchmark's dev and
test runs changed no decision; see
[the Quillon report, section 4](quillon-v1-evaluation.md#4-tuning-the-decision-policy-dev-split).

### What is still wrong after run 3

- **Distorted statements that no check catches (3, all in published articles):**
  - "Before selling or giving away a Q3 router, **the buyer** should … tap Remove
    device". The reply says "before you sell", meaning the seller.
  - "**To transfer** a Quillon Secure subscription, the customer must cancel their own
    subscription". The reply says the subscription can't be transferred, and to cancel
    it if it's no longer needed.
  - "**Secure Plus** subscriptions cannot be transferred". The reply says Quillon
    Secure, the plan family, not one plan in it.

  NLI scores all three as entailed or neutral, not contradicted. They are what an admin's
  review has to catch, and an unedited publish shows them to customers.
- **Steps paraphrased away.** The quotes for port forwarding and bridge mode contain the
  menu path ("Settings, then Advanced, then …"). The statements kept the meaning but
  lost the path. "Where is the bridge mode setting?" abstains for that reason, with the
  article retrieved at relevance 1.00. The port 25565 answer is incomplete for the same
  reason. A check can't notice content that is missing. A prompt rule to keep steps and
  menu paths word for word is the obvious next thing to try.
- **Titles aren't checked.** Run 3's port forwarding article is titled "Q3 IPv6 and
  Bridge Mode Setup", although its IPv6 statement was left out.
- **Still open from the first run:** the outdated FAQ still beats the retailer rule (two
  g04 misses, unchanged), the router gaps still mix topics, and the evaluation's own
  scoring faults remain.

The six misses in run 3, by cause:

| Cause | Misses |
| --- | --- |
| Steps paraphrased away in drafting | 2 |
| The answering model said "not found", with the article retrieved | 2 (the warranty question; the Secure plan transfer) |
| The conflict check sided with the outdated FAQ | 1 |
| Retrieval | 1 (student ID) |

## Threats to validity

- **Every fix was chosen after seeing the results, on the same topics.** This includes
  the NLI threshold, chosen on these same statements. The rise from 10 to 14 correct
  answers is measured on the data that motivated the fixes. A fresh topic set would show
  how much of it holds.
- **Small numbers.** 12 topics, 20 answerable held-out questions, one or two tickets per
  topic. The intervals are wide: 30-70% for run 1's 50%, and 48-85% for run 3's 70%. The
  grouping threshold is chosen on 16 questions.
- **Written by the people who built the system.** Topics, tickets, paraphrases and key
  facts were all written for this project. Real ticket replies are longer, mix several
  issues and repeat greetings and case details, which makes drafting harder.
- **The hand checks were done by the builder**, with the documents and tickets open. The
  verdicts are written out above so they can be disputed. The two borderline cases are
  the retailer-rule conflict (real, but the outdated side wins) and the unlink answer
  (right in substance, but incomplete).
- **Unedited publishing is the worst case**, not how the feature is meant to be used. An
  admin reading these drafts would delete the IPv6 sentences, the distorted statements
  and the off-topic return rules. Nothing here measures that.
- **One model, one run per configuration.** `qwen3.5:4b` at temperature 0 with a fixed
  seed, for both drafting and answering. Two or three misses in each run are the
  answering model's. A larger model may answer from the articles more reliably, which
  wasn't measured. Run 2 also shows that a small prompt change can change what the
  model writes in ways no single run predicts.
- **The faults above were found on the only labelled data there is.** A fix measured on
  the same topics will look better than it would on new ones.

## What this means for the product

Run on a knowledge base like this one, the feature works as a starting point for an
admin, not as an autopublisher. Finding gaps is free. Drafting a gap takes two to three
minutes. In the first run, half the questions people couldn't get answered became
answerable once the drafts were published, and no customer detail or outdated claim got
through.

In the first run, most of what went wrong went toward saying too little: one bug and
two missing prompt rules removed or skipped correct content. An admin would have to add that content back by
hand. What goes wrong toward saying too much is what an admin must catch:

- **Mixed router gaps** produce articles that cover several topics.
- **A support agent's promise to find out** reads like a fact.
- **Similar but unrelated tickets** slip statements into the wrong article.

The editor's warning that "an admin's review is the real safeguard" is accurate.

The fixes made the drafts say more: 14 of 20 held-out questions are answerable instead
of 10. They also showed the cost of saying more. One prompt rule was enough for the
model to turn a support agent's "I'll find out" into a confident "no", and only a fourth
check caught it. Three subtler distortions still pass every check. So the more complete
the drafts get, the more the admin's review matters.
