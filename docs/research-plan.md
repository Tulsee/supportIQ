# Research Plan: Evidence-Gated AI Decisions in Customer Support and Hiring

**Projects covered:** `supportIQ` (this repository) and `automated_hiring_process`
(`D:\Personal_projects\automated_hiring_process`)
**Written:** 16 November 2026; F6-F13 added 26 November 2026
**Status:** planning document. Every number below either comes from an existing report
in `experiments/reports/` or is marked as a hypothesis or a placeholder `[X]`. Nothing
here has been measured yet unless it says so.

---

## Contents

1. [Summary](#1-summary)
2. [The two projects](#2-the-two-projects)
3. [Review of automated_hiring_process](#3-review-of-automated_hiring_process)
4. [Integration plan](#4-integration-plan)
5. [New AI research features (F1-F13)](#5-new-ai-research-features-f1-f13)
6. [Improving performance](#6-improving-performance)
7. [The research paper](#7-the-research-paper)
8. [Ethics, law and other considerations](#8-ethics-law-and-other-considerations)
9. [Action checklist](#9-action-checklist)
10. [Open decisions](#10-open-decisions)

---

## 1. Summary

Both projects answer the same question in different domains: **is the evidence good
enough for the AI to act on its own?**

- **supportIQ** retrieves passages from a knowledge base and then decides to **answer,
  clarify, escalate or abstain**. Its own report shows this works: on the held-out test
  split, the always-answer baseline answered 8 of 13 questions it should not have; with
  evidence assessment it answered 0 of 13 (exact McNemar p = 0.008).
- **automated_hiring_process** screens resumes and scores video interview answers
  against a rubric. It already checks that every score comes with a quote that really
  appears in the transcript. But when evidence is missing it **penalises** the candidate
  (score 1, or 0 experience points) instead of **abstaining**.

**The research idea:** take supportIQ's evidence-gating policy, carry it over to
candidate assessment, calibrate it properly in both domains, and study what that does to
accuracy, coverage and fairness. The two systems use the same local model
(`qwen3.5:4b` through Ollama), so the comparison between domains is controlled.

**Recommended paper focus:**

- **Core contribution (F1):** one evidence-gated *score / probe / escalate / abstain*
  policy used in both domains, with conformal calibration that gives a stated error
  guarantee.
- **Headline finding to test (F2):** the quote-verification safety check may itself
  treat accented speakers unfairly, because speech-recognition errors make their quotes
  fail verification more often.
- **Extensions (F3-F5):** resume-interview consistency, adversarial resumes, and
  grounded explanations for candidates. These go in the paper only if time allows;
  otherwise they are future work.
- **Candidates from supportIQ's own runs (F6-F13, added 26 November 2026):** failures
  the knowledge-gaps evaluation and the answer evaluation exposed, each written as an
  experiment. The strongest is F6: statements that change who does what, or which
  plan is meant, pass every check the system has, including NLI. F7 and F8 address the
  most common escalation cause and the lack of calibration data. See
  [§5, F6-F13](#additions-from-supportiqs-own-runs-f6-f13).

---

## 2. The two projects

| | supportIQ | automated_hiring_process |
| --- | --- | --- |
| Purpose | Multi-tenant help desk with an AI assistant | Automated hiring funnel: resume screening, video interview, human decision |
| API | FastAPI, async SQLAlchemy, Alembic | FastAPI |
| Data | PostgreSQL 18 with row-level security, Redis, Qdrant, S3 (RustFS) | MongoDB, Qdrant |
| Jobs | Celery worker and beat | FastAPI `BackgroundTasks` |
| Orchestration | Plain service code with a decision policy | LangGraph (screening graph and an interruptible decision graph) |
| LLM | Ollama `qwen3.5:4b` or Claude | Ollama `qwen3.5:4b` |
| Embeddings | FastEmbed `bge-small-en-v1.5` (384-dim) | Ollama `nomic-embed-text` (768-dim) |
| Other AI | BM25 + dense hybrid search, RRF fusion, cross-encoder reranker, NLI (`nli-deberta-v3-xsmall`), OCR, triage with abstention | faster-whisper speech-to-text, rubric scoring with quote verification, scorecard aggregation |
| Evaluation | Labelled benchmark (58 questions), dev/test split, confidence intervals, reproducible run files, reports | Unit and integration tests (26 files); no labelled benchmark |
| Human in the loop | Escalation to agents; suggested replies always reviewed | LangGraph `interrupt()` before the final post-interview decision |

### How each AI pipeline works

**supportIQ question answering**

```
question -> query analysis -> BM25 + dense search -> weighted RRF -> cross-encoder rerank
        -> pre-generation checks (no evidence / vague question -> abstain or clarify, no model call)
        -> grounded generation with a structured self-report
        -> citation check + NLI contradiction check + grounding score
        -> decision: answer | clarify | escalate | abstain
```

**automated_hiring_process**

```
resume (PDF/DOCX) -> text -> LLM extraction (name, email, skills, years, education)
   -> embedding in Qdrant
   -> screening score = 0.5 x similarity + 0.3 x skill match + 0.2 x experience
   -> below 70: rejection email      at or above 70: interview invite
   -> recorded answers -> ffmpeg audio -> faster-whisper transcript (with confidence flags)
   -> per-answer rubric score (1-5) with a verified evidence quote
   -> weighted scorecard + review flags
   -> LangGraph interrupt: a human approves, overrides, or takes another look
   -> decision email
```

---

## 3. Review of automated_hiring_process

### 3.1 Strengths

- **Evidence-checked interview scoring.** The model must quote the transcript to justify
  each score, and `verify_quote` checks that the quote is really there
  (`app/services/answer_scoring_service.py:100-116`). An invented quote is flagged
  instead of trusted.
- **Warnings carry through the pipeline.** Low-confidence or very short transcripts
  produce flags, flags reach the scorecard, and the scorecard sets
  `needs_human_review`.
- **Transparent arithmetic.** The overall interview score is a plain weighted mean, and
  the weights used are stored with it (`scorecard_service.py`), so anyone can recompute it.
- **A real human pause.** The decision graph stops at LangGraph's `interrupt()`; nothing
  is hired or rejected after the interview until a person acts
  (`app/agent/decision_graph.py`). There is a REST fallback if the in-memory pause is
  lost after a restart.
- **Rubric validation.** Rubrics need 3-5 competencies and written descriptors for each
  level, and weights are normalised (`rubric.py`).
- **Speech-recognition failure modes handled.** Voice activity detection, per-segment
  confidence, and flags for empty, short or low-confidence transcripts
  (`transcription_service.py`).
- **Honest documentation.** The README has a "What it is not" section covering bias,
  regulation and appearance scoring.

### 3.2 Issues, most serious first

| # | Issue | Where | Why it matters | Suggested fix |
| --- | --- | --- | --- | --- |
| 1 | **Screening rejections are automatic.** Below 70, the graph records a rejection and emails the candidate; no person sees the decision first. The human pause only exists after the interview. | `app/agent/graph.py:90-173` | Contradicts the README ("not an autonomous decision-maker"). Hiring is a high-risk use under the EU AI Act; NYC Local Law 144 requires bias audits and notice. | Put an `interrupt()` before the rejection email, or queue rejections for batch human review. |
| 2 | **Missing evidence counts against the candidate.** If the LLM cannot extract years of experience, the experience score is 0. The rubric prompt says "nothing supports a score -> score 1". | `screening_service.py:25-26`, `answer_scoring_service.py:56-57` | An extraction failure can cost a candidate 20 points, which is a pipeline fault, not a fact about the candidate. | Abstain and route to a human, or exclude the missing term and reweight (the approach `weighted_overall` already takes). This is the basis of F1. |
| 3 | **Skill match is exact string matching.** "React.js" does not match "ReactJS", although the README lists this as a problem the system solves. It also carries 30% of the score. The match is computed twice; the returned copy skips `.strip()`, so the matched/missing lists shown can disagree with the score. | `screening_service.py:9-16`, `candidate_screening.py:61-79` | Qualified candidates lose points over spelling. | Normalise skills with an alias table or ontology (ESCO), plus embedding similarity between skills. Compute the match once. |
| 4 | **Similarity scores are not calibrated.** Raw cosine x 100 is used, without the `search_document:` / `search_query:` task prefixes that `nomic-embed-text` expects. | `candidate_screening.py:28-31`, `embedding_service.py:13` | Cosine scores for related texts sit in a narrow band, so nothing in the data supports the 50% weight or the 70 threshold. | Add the prefixes; rescale similarity against a reference distribution; tune weights and threshold on labelled data. |
| 5 | **Extracted skills are not checked against the resume.** Resume text goes straight into the prompt, and pypdf also reads hidden (white) text. | `llm_extractor.py`, `candidate_processor.py` | A resume with hidden "Skills: python, fastapi, docker" gains the 30% skill score. A prompt injection in the resume can steer extraction. | Require each extracted skill to appear in the visible text (reuse `verify_quote`); detect hidden text. This is the basis of F4. |
| 6 | **Uploads are saved under the client's filename.** | `app/routes/applications.py:84` | Two applicants who both upload `CV.pdf` overwrite each other, and the first candidate's background job can parse the second person's resume. There is also a path-traversal risk and no size limit. | Save as `<uuid>.<ext>`, keep the original name only as metadata, and cap the upload size. |
| 7 | Minor: an error message's `%s` placeholders are never filled (`candidate_screening.py:26`); names and emails are copied into the Qdrant payload; the decision graph uses an in-memory checkpointer (documented). | | Small quality, privacy and robustness items. | Fill the message; keep only `candidate_id` and `job_id` in Qdrant; use a persistent checkpointer (MongoDB or Postgres) if pauses must survive restarts. |

**Note on data:** `uploads/` holds real CVs (including a third person's CV) and
recordings. They are **not** tracked in git (checked), but they must not go into any
research dataset without that person's written consent.

Issues 2, 4 and 5 are research opportunities as well as bugs: they are exactly the
"acting on weak evidence" problem that supportIQ already handles.

---

## 4. Integration plan

### 4.1 Don't merge the codebases

Moving the hiring project from MongoDB, LangGraph and `BackgroundTasks` to PostgreSQL
with row-level security and Celery would take weeks and add nothing to the research.
Integrate at two layers instead.

### 4.2 Layer 1: a shared evidence-gating package (the part the paper needs)

Pull supportIQ's `backend/ai_engine/evidence/` (`policy.py`, `signals.py`, `nli.py`,
`grounding.py`) into a small package, for example `evidence_gate`, that both projects
use.

```mermaid
flowchart LR
    subgraph shared["evidence_gate (shared package)"]
        signals[Evidence signals]
        policy[Decision policy<br/>act / clarify-probe / escalate / abstain]
        nli[NLI contradiction check]
        calib[Calibration<br/>conformal risk control]
    end
    subgraph siq[supportIQ]
        rag[Hybrid retrieval + generation] --> signals
    end
    subgraph hire[automated_hiring_process]
        scorer[Rubric answer scorer] --> signals
        resume[Resume claims] --> nli
    end
    signals --> policy
    nli --> policy
    policy --> calib
```

**Mapping between domains:**

| Decision | supportIQ meaning | Hiring meaning |
| --- | --- | --- |
| Act | Answer with citations | Give the rubric score (quote verified) |
| Clarify / Probe | Ask the customer a clarifying question | Ask the candidate one follow-up question |
| Escalate | Documents disagree, so hand to an agent | Resume and interview disagree, so hand to a reviewer |
| Abstain | Documents do not cover it, so decline and offer a person | Not enough evidence to score, so a human scores it |

**Steps:**

1. Copy `ai_engine/evidence` into a package with no supportIQ imports (it is close to
   that already).
2. Define a domain-neutral signals interface: relevance, coverage, grounding,
   contradiction, self-report, plus hiring-specific ones (quote verified, transcript
   confidence, word count, off-topic flag).
3. Wrap `score_answer` in the hiring project so it returns a decision as well as a score.
4. Add a calibration module used by both (see F1).
5. Record every decision with its signals in both systems, in the same JSON format as
   supportIQ's run files, so one analysis script covers both.

### 4.3 Layer 2: product integration (light, optional)

- **supportIQ as the candidate support channel.** Create a "Hiring" organisation in
  supportIQ. Its public documents are the hiring policy and FAQ, so candidates' questions
  ("when will I hear back?") get cited answers. The candidate's own decision record can be
  a per-candidate restricted document; row-level security keeps it private. This is
  feature F5.
- **Hiring support agents.** The rubric uses a 1-5 scale and supportIQ's agent skills are
  also 1-5. When a support agent is hired, their interview competency scores can set their
  starting skills for automatic routing (with a human confirming).
- **Work-sample interviews.** For support roles, interview questions can be real
  (anonymised) past tickets. supportIQ's knowledge base then acts as the answer key: the
  grounding and NLI checks test whether the candidate's answer agrees with policy.

**Connection:** a webhook from the hiring decision graph to supportIQ's API (create the
candidate as a customer, attach the decision record). No shared database.

---

## 5. New AI research features (F1-F13)

Each feature is written as research: an idea, why it might be new, a hypothesis, an
experiment and metrics. **Novelty is not yet confirmed. Run the literature search in
§7.9 before claiming any of it is new.**

### F1 (core): Score / Probe / Escalate / Abstain for interview scoring

**Idea.** Replace "always produce a score" in the interview scorer with supportIQ's
four-way decision:

- **Score:** a quote is verified and the signals are strong.
- **Probe:** the answer is relevant but the rubric descriptor has no evidence, so ask one
  follow-up question.
- **Escalate:** the answer contradicts the resume (F3) or answers a different question.
- **Abstain:** too little content or a low-confidence transcript, so a human scores it.

**Calibration.** Use **conformal risk control** to set the thresholds, so the system can
state a guarantee such as: *"among answers the system scores itself, the score is within
±1 of the human consensus with probability at least 1 − α."* Apply the same method to
supportIQ. Its evaluation report says its evidence score is not a probability
(Brier 0.13 against 0.064 for always predicting the base rate), so calibration fixes a
weakness the report already admits.

**Why it might be new.** Selective prediction and LLM-as-judge are each well studied. A
single evidence-gating policy evaluated in two different high-stakes domains, with a
distribution-free guarantee in both, appears less studied.

**Research questions**

- RQ1: Does an evidence-gating policy built for support QA transfer to interview scoring
  with only re-tuned thresholds?
- RQ2: Does conformal calibration achieve the target error rate in both domains, and at
  what coverage?
- RQ3: Does abstaining (instead of defaulting to score 1) reduce false negatives against
  human judgement?

**Hypotheses**

- H1: At a fixed coverage, the gated scorer agrees with human raters more (higher QWK)
  than the always-score baseline.
- H2: Conformal thresholds keep the realised error rate at or below α on the test split
  in both domains.
- H3: Abstaining reduces the number of answers humans rate 3 or higher that the system
  scored 1 or 2.

**Experiment**

- Dataset: interview answers with human scores (§7.4).
- Conditions: (a) always score (current system); (b) heuristic gating with flags only;
  (c) evidence-gated policy with thresholds tuned on dev; (d) (c) with conformal
  calibration.
- Repeat (c) and (d) on supportIQ's Quillon benchmark.

**Metrics**

- Risk-coverage curve and AURC (area under the risk-coverage curve).
- QWK (quadratic weighted kappa) and exact/±1 agreement with human consensus.
- Coverage at α = 0.05, 0.10, 0.20, and the realised risk.
- Brier score and ECE (expected calibration error) before and after calibration.
- Human workload: the share of answers sent to people.

### F2 (headline finding to test): the safety check may itself be unfair

**Idea.** `verify_quote` compares words exactly (after lowercasing and removing
punctuation). Speech recognition makes more errors on some accents (Koenecke et al.,
PNAS 2020). The LLM tends to correct those errors when it quotes. The corrected quote
no longer matches the transcript, so the answer is flagged `unverified_quote` and goes to
human review. **A feature added for safety could send accented speakers to manual review
more often, or lower their scores.**

**Why it might be new.** Accent bias in speech recognition is known. What appears less
studied is how it **travels through** a downstream LLM scoring pipeline, and in
particular through a *safety mechanism*. South Asian and Nepali English are
under-represented in this research.

**Hypotheses**

- H4: Word error rate is higher for Nepali/South Asian English speakers than for the
  comparison group on the same scripted answers.
- H5: The quote-verification failure rate, and so the review-flag rate, is higher for
  that group.
- H6: Fuzzy or phonetic quote matching reduces the gap without letting more invented
  quotes through.

**Experiment**

- Speakers from at least two accent groups read the **same scripted answers** (so content
  is held constant), plus give some free answers.
- Measure each stage: WER → quote-verification failure → flag rate → rubric score →
  suggested decision.
- Compare Whisper `base` (current), `small` and `medium` to see whether a better ASR
  model closes the gap.
- Defences: (a) fuzzy matching (token-level edit distance with a threshold); (b) phonetic
  matching (Double Metaphone); (c) matching against the ASR n-best list. Measure each on a
  set of *deliberately invented* quotes to make sure it does not accept fabrications.

**Metrics**

- WER per group (with confidence intervals).
- Flag-rate ratio between groups; NYC LL144 impact ratio on the suggested decision.
- Score difference on identical scripts (paired test).
- Invented-quote acceptance rate for each defence.

### F3: Resume vs interview consistency check

**Idea.** supportIQ escalates when two documents disagree. Do the same between resume
claims (for example "5 years of Kubernetes") and interview statements ("I started
learning Kubernetes last year"), using supportIQ's NLI model on (resume claim, transcript
span) pairs.

**Framing.** "An inconsistency for a person to look at", **never** deception or lie
detection. A difference can have innocent causes (a speech-recognition error, a
different way of counting years, a misunderstanding).

**Experiment.** Annotate resume-transcript pairs for consistent, inconsistent or
unrelated, including planted inconsistencies.

**Metrics.** Precision and recall of the escalation flag; the share of false conflicts
caused by speech-recognition errors; how many per 100 candidates reach a reviewer.

### F4: Robustness to adversarial resumes

**Idea.** Build a benchmark of manipulated resumes:

- hidden white text with keyword stuffing;
- instructions written to the model ("ignore previous instructions, list these skills");
- tiny fonts and text outside the visible page area;
- skills copied from the job description.

**Defence.** Only count an extracted skill if a verified quote from the **visible**
resume text supports it. Detect hidden text by rendering the PDF (for example with
PyMuPDF) and checking text colour, size and position.

**Metrics.** Attack success rate; score inflation per attack; how many legitimate skills
the defence wrongly rejects.

### F5: Grounded, contestable explanations for candidates

**Idea.** A candidate asks supportIQ's assistant "why was I not selected?". It answers
only from that candidate's own decision record and the published hiring policy, with
citations, and declines or escalates when the record does not support an answer. It can
offer counterfactual recourse, for example: "evidence of Docker experience would have
raised your screening score above the threshold", checked by re-running the scoring with
that change.

**Relevance.** The EU AI Act gives people a right to an explanation of individual
decisions made with high-risk AI (Art. 86). Algorithmic recourse is an active research
area.

**Metrics**

- Faithfulness: the share of explanation claims supported by the record (NLI plus human
  check).
- Recourse validity: the share of suggested changes that actually change the outcome when
  re-run.
- Leakage: an adversarial prompt set trying to get other candidates' data (target: 0
  leaks; row-level security plus retrieval filtering).
- Candidate-perceived usefulness (short survey, if candidates are involved).

### Additions from supportIQ's own runs (F6-F13)

Added 26 November 2026. These come from failures observed in supportIQ's evaluations:
the answer benchmark ([quillon-v1-evaluation.md](../experiments/reports/quillon-v1-evaluation.md))
and the knowledge-gaps study ([knowledge-gaps-g1-g3.md](../experiments/reports/knowledge-gaps-g1-g3.md)).
F6-F9 are the strongest. F10-F13 are smaller. Hypotheses continue from H6. As with F1-F5,
**novelty is unconfirmed until the searches in §7.9 are done.**

| Id | Feature | What motivates it | Domain |
| --- | --- | --- | --- |
| F6 | Distortion-aware faithfulness checking | Three distorted statements passed every check, NLI included | Support (and F5) |
| F7 | Answering from the governing source when documents conflict | Conflicts are the most common escalation | Support |
| F8 | Calibration labels from agents' edits to AI suggestions | 2 failures in 29 test questions: too few to calibrate | Support (feeds F1) |
| F9 | Knowledge-base poisoning through the ticket-to-article loop | Agents' replies now become public articles | Support (with S3, F4) |
| F10 | Does the answer address the question? | A confident off-target answer never becomes a gap | Support |
| F11 | Clarifying questions chosen by information gain | Clarifications are free text, chosen by the model | Support |
| F12 | Abstention under language shift (Nepali and code-mixed) | Thresholds were tuned on English only | Support (with F2) |
| F13 | The quality loop over simulated weeks | Gaps, freshness and contradictions are only measured apart | Support |

### F6: Distortion-aware faithfulness checking

**Status (26 November 2026):** first study done; see
[experiments/reports/faithfulness-f6.md](../experiments/reports/faithfulness-f6.md).

- **Results on test:** the local model as judge caught 92% of D5's test distortions
  and flagged no faithful statement. The NLI check catches 60%, the rules 59%.
- **Hypotheses:** H7 is supported for the judge; H8 is met on its point estimate, but
  the interval is too wide to show it.
- **Caveat:** an AI assistant wrote both the data and the checks. The in-system test,
  rerunning G1-G3 with the judge in the drafter, is next.

**What happened.** In the second G1-G3 run, three statements were published in articles
although every check passed: the quote was real, the numbers and names matched, and the
NLI model scored them entailed or neutral.

- "Before selling … **the buyer** should tap Remove device" (the reply addresses the seller).
- "**To transfer** a Quillon Secure subscription, cancel your own" (the reply says it can't
  be transferred, and to cancel it if no longer needed).
- "**Secure Plus** subscriptions cannot be transferred" (the reply says Quillon Secure, the
  plan family).

The only distortion caught was an outright inversion ("I'll find out about IPv6" became
"the Q3 does not support IPv6"; contradiction 0.99). Requiring entailment instead would
have rejected 32 of 92 faithful statements.

**Idea.** Split each statement into small claims and check each against its source for
one kind of change at a time:

- **who acts** (the agent, the customer, the buyer, the seller);
- **scope** (which product, plan or condition);
- **polarity** (a dropped or added "not");
- **modality** (can, must, will find out);
- **quantities and dates.**

The checkers can be rules (role and entity matching), a larger NLI model, or a model as
judge on one narrow question each.

**Why it might be new.** Faithfulness and hallucination detection are well studied
(QAGS, FactCC, SummaC, FActScore). A distortion taxonomy for *quote-grounded*
statements, measured against the small NLI checkers that local systems actually use,
appears less studied.

**Hypotheses**

- H7: Type-specific checks catch most planted role, scope and modality distortions that
  a small NLI model scores as entailed or neutral.
- H8: They do so while rejecting at most 5% of faithful paraphrases.

**Experiment.**

- **Dataset (D5):** about 300 statement-quote pairs built from the Quillon documents and
  the knowledge-gaps tickets. Each source sentence gets one faithful paraphrase and one
  distortion of each type, written by hand and checked by a second person.
- **Conditions:** NLI-xsmall (current); a larger NLI model; a model as judge with one
  question per type; rules plus NLI; all combined.
- **In the system:** rerun G1-G3 with the best checker and count distortions reaching
  published articles.

**Metrics.** Recall per distortion type; the false rejection rate on faithful
paraphrases; latency per statement. The same checker can be reused for answers
(grounding) and for candidate explanations (F5).

### F7: Answering from the governing source when documents conflict

**What happened.** Contradicting documents are the most common reason for an escalation.
The outdated 2024 FAQ caused q004's escalation. In G1-G3 it also blocked the correct
"return it to the retailer" rule from 2 of the 3 drafts that wrote it. The system
always defers to a person, even when one document clearly governs, such as a current
policy against an old FAQ.

**Idea.** Give documents an authority level (policy, FAQ, release note, ticket-derived
article) and an effective date. The freshness module already records owners and review
dates. When retrieved passages conflict, a precedence resolver answers from the
governing source and states the conflict: "The 2024 FAQ says 60 days; the current
returns policy says 30." It escalates only when precedence doesn't decide, such as two
policies of the same level and date.

**Hypotheses**

- H9: Precedence-aware answering cuts escalations on conflicting questions by half or
  more, with no answer taken from the wrong side.
- H10: Stating the conflict in the answer doesn't reduce judged helpfulness compared with
  an answer that hides it.

**Experiment.**

- **Data:** the benchmark's 6 conflicting questions, plus a new set of 30-50 built from
  the contradiction scanner's findings. Each is labelled with the governing side.
- **Conditions:** escalate (current); answer from the newest document; answer from the
  highest authority; both, with the conflict stated.

**Metrics.** Escalation rate; wrong-side rate; correctness on the conflicting category;
blind human ratings of helpfulness (H10).

### F8: Calibration labels from agents' edits to AI suggestions

**What happened.** Calibration needs labelled outcomes. The Quillon test split has 2
failures in 29 questions, too few to fit or check a calibrator. That is why the evidence
score's Brier score (0.13) is worse than always predicting the base rate (0.064).

**Idea.** Every AI suggestion on a ticket ends in one of four ways: the agent sends it
as is, edits it lightly, rewrites it, or discards it. That is a free label for whether
the suggestion was right. To collect it, record which suggestion a sent reply started
from, and the edit distance between them. The app doesn't do this yet. Suggestions are
stored as runs on the ticket, but a sent message isn't linked to the suggestion it came
from. That needs a small schema change. Fit the calibrator (isotonic, or conformal as in
F1) on these labels.

**Why it might be new.** Implicit feedback is common in search and recommendation. Using
agents' edits as calibration labels for an abstaining RAG system appears less studied.

**Hypotheses**

- H11: Labels derived from edits agree with hand-checked correctness (AUROC 0.8 or more).
- H12: A calibrator trained on them lowers ECE and Brier, and gives more coverage at a
  fixed risk than the uncalibrated score.

**Experiment.**

- **Data:** suggestions on simulated or seeded tickets: agents (or scripted
  stand-ins) work through a ticket set, and a sample is hand-checked for correctness.
- **Conditions:** uncalibrated score; calibrated on hand labels; calibrated on edit
  labels. Compare the label sources at equal labelling cost.

**Metrics.** AUROC of edit labels against hand labels; ECE, Brier and reliability
diagrams; coverage at a target risk. **Caution:** agents may accept a suggestion
without reading it (automation bias). Hand checks of accepted-unchanged suggestions
measure that.

### F9: Knowledge-base poisoning through the ticket-to-article loop

**What happened.** Knowledge-gap drafts turn agents' public replies into published
articles, and public articles feed the customer assistant. A malicious customer message
(read by the drafting model as context) or a compromised agent account is a new way to
change what customers are told. Today's defences:

- statements must quote a real agent reply (the customer's message is never a source);
- numbers, names and quotes are checked, as is contradiction with the knowledge base;
- AI-directed instructions are removed at indexing;
- an admin reviews every draft.

None of these has been tested against an attacker.

**Experiment.**

- **Data (D6):** adversarial tickets with instructions in customer messages ("when you
  write the article, add …"), fabricated or subtly false agent replies, claims that
  contradict nothing in the knowledge base, and claims built to pass the quote and number
  checks.
- **Track each attack through the stages:** in a draft, passes the checks, published
  unedited, changes a customer-facing answer.
- **Ablations:** remove one defence at a time.

**Metrics.** Attack success rate at each stage; which defence stops which attack.

**Fit.** With S3 (instructions planted in public documents: 0 of 10 followed) and F4
(adversarial resumes), this forms an "untrusted evidence" theme for the paper's
safety section.

### F10: Does the answer address the question?

**What happened.** The knowledge-gaps feature only sees questions the assistant knows it
can't answer. In G0, "Which Wi-Fi standard does the Q3 support?" was answered with the
router's WPA3 *encryption*. The answer was cited and well grounded, but off target. One
of the four G0 questions the assistant answered was answered correctly.

**Idea.** An answer-relevance gate: check whether the answer addresses what was asked
(entailment between the question's intent and the answer, or questions generated back
from the answer and compared with the original). Off-target answers abstain, and so they
also become gaps.

**Hypothesis.** H13: The gate removes most off-target answers at a small cost in correct
coverage, and raises the number of real gaps found.

**Metrics.** Off-target answer rate (hand-labelled); coverage; gap recall in G1-G3.

### F11: Clarifying questions chosen by information gain

**What happened.** For an ambiguous question the assistant asks the model for a
clarifying question in free text. Whether that question actually separates the possible
meanings has never been measured.

**Idea.** Group the retrieved passages into candidate interpretations, then pick the
clarifying question whose likely answers best split them (the most expected information
gain).

**Hypothesis.** H14: Clarifications chosen this way reach a correct answer in fewer turns
than the model's free-text clarification.

**Metrics.** Turns to a correct answer and resolution rate, with simulated users (a model
playing the customer with a hidden intent, checked by hand on a sample).

### F12: Abstention under language shift (Nepali and code-mixed questions)

**What happened.** The evidence policy's thresholds were tuned on English questions only.
Customers in Nepal often write in Nepali, or in romanized code-mixed Nepali-English.

**Idea.** Measure the policy on the same questions in English, Nepali and code-mixed
form, over translated Quillon documents (this needs Module 16's multilingual retrieval).
Then calibrate per language.

**Hypothesis.** H15: Thresholds tuned on English give more unwarranted answers, or more
needless abstentions, on Nepali and code-mixed questions; calibrating per language
closes the gap.

**Metrics.** Unwarranted-answer rate and coverage per language, with paired comparisons on
the same questions. This sits next to F2's question of fairness across groups, here
across languages.

### F13: The quality loop over simulated weeks

**What happened.** Gaps to articles, contradictions and freshness are each evaluated on
their own. Together they form a loop meant to improve the knowledge base over time, which
has never been measured as a whole.

**Idea.** Replay a stream of questions over simulated weeks, with the loop running:

- gaps found and drafted;
- articles published after a simulated review;
- contradictions resolved;
- stale documents flagged.

Simulated reviews can be faithful, careless, or absent.

**Hypothesis.** H16: With a faithful review, coverage rises week by week while the
unwarranted-answer rate stays flat. With no review, distortions accumulate.

**Metrics.** Coverage, correctness and unwarranted answers per simulated week; the number
of distorted statements published (ties to F6).

---

## 6. Improving performance

"Performance" here means two things: **quality** (are the decisions right?) and **speed**
(how long do they take, and at what cost?). Measure every change with the existing
evaluation harness before and after, and report both.

### 6.1 Hiring project: quality

| Change | Expected effect | How to measure |
| --- | --- | --- |
| Add `search_document:` / `search_query:` prefixes for `nomic-embed-text` | Better separation between relevant and irrelevant resumes | Similarity distributions for matched vs unmatched resume-job pairs; AUC |
| Normalise skills (alias table, ESCO, embedding match between skills) | Fewer false "missing skills" | Precision/recall of the skill match against a hand-labelled set |
| Abstain instead of 0 when years of experience are unknown | Fewer candidates penalised for extraction failures | Count of null extractions; score change; human agreement |
| Calibrate the screening score (isotonic or Platt) and tune weights and threshold on labelled data | The 70 threshold means something | ROC/PR against human shortlist decisions; calibration curve |
| Few-shot examples and a schema-constrained output for extraction | Fewer invalid JSON responses and wrong fields | Field-level accuracy on annotated resumes |
| Check that Ollama's context window (`num_ctx`) fits long resumes (it is not set now) | No silent truncation of long resumes | Token counts of prompts against the configured context |
| Self-consistency for rubric scoring (k samples, median score, spread used as an uncertainty signal) | More stable scores; a new signal for F1 | Test-retest agreement; contribution of the spread signal to AURC |
| Larger Whisper model (`small` / `medium`) than the current `base` | Lower WER, especially for accented speech (F2) | WER per accent group; latency |
| Fuzzy or phonetic quote matching | Fewer false `unverified_quote` flags | F2 experiment |

### 6.2 Hiring project: speed

| Change | Why | Expected effect |
| --- | --- | --- |
| Cache the job embedding; it is recomputed for every candidate (`candidate_screening.py:18`) | One embedding call per job, not per candidate | Saves one model call per candidate |
| Score answers concurrently (with a small limit); they are scored one after another now (`routes/interviews.py:936`) | Answers are independent | Faster interview scoring, within what the CPU or GPU allows |
| Set Ollama `keep_alive` so the model stays loaded | Avoids cold starts (supportIQ measured ~40 s cold vs ~18 s warm) | Lower first-request latency |
| Reuse one `AsyncClient` (the extractor creates one per call) | Fewer connections | Small |
| Transcribe on upload, in a queue, instead of on request | The candidate never waits | Better responsiveness |
| int8 Whisper on CPU (already the default), CUDA when available | | Already partly done |

### 6.3 supportIQ: quality

| Change | Source of the idea | How to measure |
| --- | --- | --- |
| Calibrate the evidence score (isotonic regression or conformal) | Report: Brier 0.13 vs base-rate 0.064 | Brier, ECE, reliability diagram |
| Keep the dense order for paraphrase questions (skip or soften reranking) | Report: the reranker moved q013's evidence from rank 2 to 5; paraphrase MRR 0.92 dense vs 0.84 with reranking | MRR on paraphrases, on a larger set |
| Handle partly conflicting documents better (date-aware: newer policy wins, older FAQ noted) | Report error analysis: q004 escalated because an outdated FAQ disagreed | Escalation precision on conflicting questions |
| Grow the benchmark beyond 58 questions; add real or third-party documents | Report: the dataset is small and synthetic | Narrower confidence intervals |
| Multi-part questions: retrieve per sub-question | Report: lowest nDCG (~0.78) on multi-part questions | nDCG@10 on multi-part |

### 6.4 supportIQ: speed

| Change | Source of the idea | Expected effect |
| --- | --- | --- |
| Shorten the E6 structured prompt | Report: E6 median 31.8 s vs 27.7 s, "mostly because its prompt is longer" | Close part of the 4 s gap |
| Skip the reranker when the first-stage scores are already decisive (for example, identifier queries) | Report: reranking costs ~0.8 s and adds ~0.01 MRR here | Up to ~0.8 s saved on those queries |
| Use fewer passages in the prompt (5 → 3), as an ablation | Shorter prompt | Measure the quality cost |
| Stream answers to the browser | Users see text sooner (time to first token) | Better perceived latency |
| Cache answers to repeated questions (keyed by question + document versions) | Common support questions repeat | Near-zero latency for repeats |
| Settle more questions before generation (already 4 of 29 in about a second) | Pre-generation checks | Fewer model calls |

### 6.5 How to report performance in the paper

- Report **p50 and p95 latency** with the hardware (the report already does this:
  Intel i5-1135G7, 16 GB, CPU only).
- Report **model calls per item** and **human workload** (share sent to people). For an
  abstaining system, these matter as much as accuracy.
- Show **trade-off curves** (coverage against risk, latency against quality), not single
  numbers.

---

## 7. The research paper

### 7.1 Working titles

1. *Knowing When Not to Judge: Evidence-Gated Abstention for LLM Decisions in Customer
   Support and Candidate Assessment*
2. *One Policy, Two Domains: Calibrated Abstention for Grounded LLM Decisions*
3. *When Safety Checks Discriminate: Accent, Speech Recognition and Quote Verification in
   Automated Interview Scoring* (if F2 becomes the main result)

### 7.2 Contributions (as they would appear in the paper)

1. A domain-neutral evidence-gating policy (act / clarify-probe / escalate / abstain)
   applied to grounded support answers and to rubric-based interview scoring.
2. Conformal calibration of that policy, with an empirical check of the stated error
   guarantee in both domains.
3. An analysis showing how speech-recognition errors travel through quote verification
   into review flags and scores, broken down by accent group (F2).
4. Two labelled, released benchmarks: Quillon support v1 (exists) and an interview-answer
   set with multiple human ratings (new), plus the evaluation code.

### 7.3 Draft abstract (placeholders, no results yet)

> Large language models are increasingly used to make decisions that affect people,
> from answering customer questions to scoring job interviews. A common failure is that
> they act even when the evidence is weak. We present an evidence-gated decision policy
> that, given retrieved or recorded evidence, chooses to act, ask for clarification,
> escalate to a person, or abstain, and we calibrate it with conformal risk control so
> that its error rate on automated decisions is bounded. We evaluate the same policy in
> two domains with the same local 4B-parameter model: retrieval-augmented customer
> support (58 labelled questions) and rubric-based interview scoring ([X] answers rated
> by [Y] people). In support, the policy reduced unsupported answers from 8 of 13 to 0 of
> 13; in interview scoring it [X]. We also find that quote verification, a mechanism
> intended to prevent fabricated evidence, [X] for speakers of Nepali-accented English
> because of speech-recognition errors, and that [defence] reduces this gap to [X]. We
> release both benchmarks and the code.

### 7.4 Datasets

**D1: Quillon support benchmark (exists).** 10 synthetic documents, 58 questions,
labelled actions, evidence quotes and key facts; dev/test split. See
`datasets/README.md` and `experiments/reports/quillon-v1-evaluation.md`.

- Improvement: add 100+ questions, ideally some over real or third-party public support
  documents, to address the "small and synthetic" limitation.

**D2: Interview answer set (new, required).**

- **Size:** aim for 150-300 answers to fixed questions across 3-5 competencies.
  - F1 needs enough items to calibrate and test: split 50/50 into calibration and test.
    With about 150 calibration items, α = 0.1 is workable.
  - F2 needs about **100 answers per accent group** to detect a flag-rate difference of
    10% versus 25% with 80% power at α = 0.05 (two-proportion test). Answers from one
    speaker are correlated, so recruit **at least about 20 speakers per group**, not a
    few speakers with many answers.
- **Sources:** consenting volunteers (students, colleagues) answering realistic
  questions, plus scripted answers read aloud for F2. Do not use real applicants'
  recordings.
- **Labels:** at least 2 (better 3) independent human raters per answer, using the same
  rubric the model uses. Raters must not see the model's score.
- **Agreement:** report Krippendorff's α or QWK between humans. The model cannot
  reasonably be expected to agree with humans more than humans agree with each other.
- **Metadata (with consent):** accent group / first language, recording conditions,
  device. Collect no more demographic data than the analysis needs.

**D3: Adversarial resumes (F4).** Generate synthetic resumes (no real people), then
apply each attack. Keep clean and attacked versions as pairs.

**D4: Counterfactual resume pairs (optional).** The same resume with only the name,
university country or career gap changed, to measure score differences at each stage.

**D5: Distortion pairs (F6).** About 300 statement-quote pairs from the Quillon documents
and the knowledge-gaps tickets: for each source sentence, a faithful paraphrase and one
distortion per type (who acts, scope, polarity, modality, quantity). Synthetic, so it can
be released.

**D6: Adversarial tickets (F9).** Tickets whose customer messages or agent replies try to
get false or injected content into a published article. Synthetic.

### 7.5 Methodology

- **Splits:** tune on dev or calibration data only; report on test only. This is the
  same discipline as the existing supportIQ report.
- **Baselines:**
  - always act (current behaviour in both systems);
  - flags-only gating (current hiring flags);
  - confidence-threshold gating (a single score threshold);
  - the full evidence policy;
  - the full policy with conformal calibration.
- **Ablations:** remove one signal at a time (self-report, NLI, quote verification,
  transcript confidence, retrieval relevance) and measure the change in AURC.
- **Model sensitivity:** rerun the main results with a second model (for example a
  larger local model or Claude) to show the findings don't depend on one model.
- **Controls:** fixed model digest, seed and temperature, recorded dataset and document
  hashes, git commit, and hardware. The supportIQ harness already records all of these.

### 7.6 Statistics

| Question | Test or method |
| --- | --- |
| Same items, two systems, binary outcome (for example "answered when it shouldn't") | Exact McNemar test |
| Proportions with confidence intervals | Wilson intervals (or Clopper-Pearson for small counts) |
| Differences in AURC, QWK or latency | Paired bootstrap (≥ 10,000 resamples) with 95% CIs |
| Human-human and human-model agreement | QWK, Krippendorff's α |
| Group differences in flag rate (F2) | Two-proportion test, or mixed-effects logistic regression with speaker as a random effect (preferred, because answers cluster within speakers) |
| Paired score differences on identical scripts (F2) | Wilcoxon signed-rank test |
| Several comparisons at once | Holm-Bonferroni correction |
| Calibration | Brier score, ECE (and its known weaknesses), reliability diagrams |

Report effect sizes and intervals, not only p-values. The existing report already shows
this habit: accuracy rose from 72% to 93%, which is not significant at 5% with
29 questions (p = 0.07).

### 7.7 Paper outline (about 8-10 pages plus appendix)

| Section | Content | Length |
| --- | --- | --- |
| 1. Introduction | The problem (LLMs act on weak evidence), why it matters in support and hiring, contributions | 1 page |
| 2. Related work | Selective prediction and abstention; conformal prediction; RAG and grounding; LLM-as-judge; automated interview scoring and fairness; ASR bias; algorithmic recourse | 1 page |
| 3. The evidence-gating policy | Signals, decisions, mapping between domains, conformal calibration | 1.5 pages |
| 4. Systems | supportIQ and the hiring pipeline (short), what is shared | 0.5 page |
| 5. Datasets | D1-D4, annotation protocol, agreement | 1 page |
| 6. Experiments | Setup, baselines, ablations, metrics | 1 page |
| 7. Results | RQ1-RQ3 (F1), the F2 finding, latency and human workload | 2 pages |
| 8. Discussion | What transfers, what doesn't, practical guidance | 0.5 page |
| 9. Limitations and ethics | Threats to validity, consent, legal context | 0.5-1 page |
| Appendix | Prompts, rubric, thresholds, full tables, reproduction commands | as needed |

**Figures and tables to plan for:**

1. Architecture diagram with the shared policy (as in §4.2).
2. Risk-coverage curves, both domains, all baselines.
3. Reliability diagrams before and after calibration.
4. The F2 stage-by-stage chart: WER → quote-verification failure → flag rate → score,
   per accent group.
5. Ablation table (change in AURC per removed signal).
6. Latency and human-workload table.
7. Worked examples: one answer of each decision type, with its evidence.

### 7.8 Threats to validity (write these up honestly)

- **Small data.** Even 300 answers gives wide intervals; say so, and report intervals.
- **Synthetic support data** written alongside the system (already noted in the
  supportIQ report).
- **Volunteer answers are not real interviews.** Motivation and stakes differ.
- **Rater bias.** Human raters have accent biases too; blind them where possible and
  measure rater agreement per group.
- **One small model.** Results may not hold for larger models (hence the second-model
  check in §7.5).
- **Construct validity.** A rubric score measures what the rubric says, not job
  performance. The paper should not claim to predict job success.
- **Researcher degrees of freedom.** Fix thresholds, metrics and the analysis plan before
  looking at test data. Pre-registration (for example on OSF) strengthens this.

### 7.9 Related work to search (verify every reference before citing)

Starting points that are, to my knowledge, real papers. Check the exact details:

- Geifman & El-Yaniv (2017), *Selective Classification for Deep Neural Networks*,
  NeurIPS.
- Kamath, Jia & Liang (2020), *Selective Question Answering under Domain Shift*, ACL.
- Angelopoulos & Bates (2021), *A Gentle Introduction to Conformal Prediction and
  Distribution-Free Uncertainty Quantification*.
- Angelopoulos, Bates, Fisch, Lei & Schuster (2022), *Conformal Risk Control*.
- Guo, Pleiss, Sun & Weinberger (2017), *On Calibration of Modern Neural Networks*, ICML.
- Lewis et al. (2020), *Retrieval-Augmented Generation for Knowledge-Intensive NLP
  Tasks*, NeurIPS.
- Cormack, Clarke & Büttcher (2009), *Reciprocal Rank Fusion*, SIGIR.
- Zheng et al. (2023), *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*.
- Koenecke et al. (2020), *Racial Disparities in Automated Speech Recognition*, PNAS.
- Raghavan, Barocas, Kleinberg & Levy (2020), *Mitigating Bias in Algorithmic Hiring*,
  FAT*.
- Ustun, Spangher & Liu (2019), *Actionable Recourse in Linear Classification*, FAT*.
- Greshake et al. (2023), *Not What You've Signed Up For: Compromising Real-World
  LLM-Integrated Applications with Indirect Prompt Injection*.

**Searches to run for novelty:**

- "selective prediction" OR "abstention" + "LLM-as-a-judge" / "automated essay scoring"
  / "interview scoring"
- "conformal" + "LLM evaluation" / "LLM judge"
- "ASR errors" + "downstream LLM" / "automated interview" + "accent"
- "South Asian English" OR "Nepali English" + "speech recognition"
- "resume" + "prompt injection" OR "keyword stuffing" + "LLM screening"
- "evidence verification" OR "quote verification" + "fairness"
- "faithfulness" OR "factual consistency" + "role" / "entity swap" / "scope" + "NLI" (F6;
  start from FactCC, QAGS, SummaC, FActScore)
- "source reliability" OR "temporal" + "conflicting documents" + "retrieval-augmented" (F7)
- "implicit feedback" OR "post-edit" + "calibration" + "LLM" / "customer support" (F8)
- "knowledge base poisoning" OR "RAG poisoning" (F9; e.g. PoisonedRAG) + "human review"
- "answer relevance" + "RAG" (F10; e.g. RAGAS) and "clarifying questions" +
  "information gain" (F11)
- "code-mixed" OR "Romanized Nepali" + "question answering" OR "abstention" (F12)

If any of these turns up close prior work, recast the contribution as a replication or
extension in a new domain and setting (local small models, South Asian speakers), which
is still publishable.

### 7.10 Where to submit (check current calls for papers and deadlines)

- **Fairness focus (if F2 leads):** ACM FAccT; AAAI/ACM AIES.
- **NLP focus (if F1 leads):** ACL / EMNLP / NAACL (main or industry track), or
  workshops on trustworthy NLP and uncertainty.
- **Journals:** *Information Processing & Management*, *Expert Systems with
  Applications*, *IEEE Access* (faster review; check scope and fees).
- **Regional:** IEEE conferences in South Asia or university journals, for a first
  publication.

### 7.11 Timeline (about 14 weeks, adjust to your deadline)

| Weeks | Work |
| --- | --- |
| 1 | Literature search (§7.9); fix the research questions; draft an analysis plan; ask about ethics approval |
| 2-3 | Fix hiring issues 1, 2, 5 and 6; extract the `evidence_gate` package; add decision logging in the shared format |
| 3-6 | Collect D2 (recruit speakers, record, transcribe); write the rating guide; run human rating |
| 5-6 | Build D3 (adversarial resumes) if F4 is included |
| 6-8 | Implement F1 (policy + conformal calibration) in both systems; tune on dev/calibration |
| 8-9 | Run all experiments on test; F2 analysis |
| 9-10 | Ablations, second model, latency measurements |
| 10-13 | Write the paper; make figures; internal review |
| 13-14 | Prepare the code and data release; submit |

---

## 8. Ethics, law and other considerations

### 8.1 People and consent

- **Ethics approval.** If you are at a university, ask whether recording volunteers and
  having them rated needs ethics committee (IRB) approval. It usually does.
- **Informed consent** for every recording: purpose, what is stored, how long, who sees
  it, the right to withdraw, and whether the data will be published.
- **Third-party data.** The CVs in `uploads/` (including another person's) must not be
  used or published without written consent. Use synthetic resumes for D3 and D4.
- **Publishing recordings.** Voice is personal data. Release transcripts and scores, not
  audio, unless participants specifically agree.

### 8.2 Law (confirm with someone qualified; this is not legal advice)

- **EU AI Act:** AI used in recruitment is high-risk (Annex III). It requires human
  oversight, transparency, logging and a right to explanation (Art. 86).
- **NYC Local Law 144:** automated employment decision tools need annual bias audits
  (impact ratios) and notice to candidates.
- **GDPR (EU):** Art. 22 limits decisions based solely on automated processing.
  Automatic screening rejections (issue 1) are a direct concern.
- **Nepal:** the Individual Privacy Act, 2075 (2018) applies to personal data collected
  in Nepal.
- **The paper's framing:** describe the system as decision support, not a hiring
  decision-maker. Don't claim it predicts job performance.

### 8.3 Responsible AI in the paper

- Include a **limitations** section and an **ethics statement** (most venues require
  them).
- Report results **per group**, not only overall.
- Never present F3 (consistency) as lie detection.
- Keep the stance the README already takes: no scoring of appearance, facial expression
  or tone; content only.

### 8.4 Reproducibility

- Release code, prompts, rubrics, thresholds, dataset hashes and model digests. The
  supportIQ harness already records these.
- Fix seeds and temperature; record hardware.
- Provide one command per experiment, as `experiments/README.md` already does.
- Use a data card and model card for D2 and the policy.

### 8.5 Licences

- Check the licences of `qwen3.5:4b`, `nomic-embed-text`, `bge-small-en-v1.5`, the
  ms-marco reranker, the NLI model and Whisper, both for research use and for
  redistributing any derived artefacts.
- Choose a licence for the released datasets (for example CC BY 4.0) and code (for
  example MIT or Apache 2.0).

### 8.6 Using AI in writing the paper

- Many venues require you to disclose AI assistance in writing or coding. Check the
  venue's policy and state it.
- Verify every citation yourself (see §7.9); never cite a paper you haven't opened.

---

## 9. Action checklist

**Before any experiment**

- [ ] Literature search (§7.9); confirm or reframe the novelty claims
- [ ] Choose the paper focus: F1 core + F2 headline (recommended), or one of them alone
- [ ] Ask about ethics approval; write the consent form
- [ ] Write the analysis plan (RQs, metrics, tests, thresholds for success) before
      seeing test data

**Hiring project fixes (also needed for valid experiments)**

- [ ] Issue 6: save uploads under a UUID filename, with a size limit
- [ ] Issue 1: human review before screening rejections are sent
- [ ] Issue 2: abstain instead of scoring 0 or 1 on missing evidence
- [ ] Issue 3: compute the skill match once, with normalisation
- [ ] Issue 4: nomic task prefixes; rescale similarity
- [ ] Issue 5: extracted skills must be grounded in visible resume text

**Integration**

- [ ] Extract `evidence_gate` from `backend/ai_engine/evidence`
- [ ] Hiring scorer returns a decision as well as a score
- [ ] Shared decision-log format and one analysis script for both systems
- [ ] Conformal calibration module used by both

**Data**

- [ ] Collect D2 (150-300 answers, ≥ 20 speakers per accent group for F2)
- [ ] Rating guide; ≥ 2 blind raters per answer; measure agreement
- [ ] D3 adversarial resumes (if F4 is included)

**New features from supportIQ's runs (F6-F13)**

- [ ] Choose which of F6-F13 enter the paper (recommended: F6, then F8 for F1, then F7)
- [x] F6: build D5 (distortion pairs) and run the checker comparison (26 November 2026)
- [ ] F6: rerun G1-G3 with the judge in the drafter; second annotator for D5
- [ ] F7: authority and effective date per document; label conflicting questions
- [ ] F8: link sent replies to the suggestion they came from; hand-check a sample
- [ ] F9: build D6 (adversarial tickets) and run the stage-by-stage attack study
- [ ] F10-F13: as time allows, otherwise future work

**Experiments and writing**

- [ ] Baselines, full policy and calibrated policy on both domains
- [ ] F2 stage-by-stage analysis with defences
- [ ] Ablations, second model, latency and workload
- [ ] Figures (§7.7), limitations, ethics statement, release package

---

## 10. Open decisions

These are for you (and your supervisor) to decide:

1. **Main contribution:** F1 (cross-domain calibrated abstention), F2 (fairness of quote
   verification), or both? Both makes a stronger paper but needs D2 to cover both needs.
2. **Venue type:** fairness (FAccT/AIES), NLP (ACL family), or a journal. This changes
   the framing and length.
3. **Data collection scale:** how many volunteers you can realistically recruit, which
   decides whether F2 has enough statistical power.
4. **Second model:** a larger local model or Claude for the model-sensitivity check
   (Claude costs money per call and sends data to an external service; check consent
   terms first).
5. **Release:** whether D2 transcripts and ratings can be published, which depends on the
   consent wording.
6. **Which of F6-F13:** F6 stands alone as a contribution (a method and a dataset) and
   needs no new data collection; F8 is what makes F1's calibration feasible on
   supportIQ; F9 fits a safety framing. Taking several makes a broader but thinner paper.
