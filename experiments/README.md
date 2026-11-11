# Experiments

Each evaluation run writes two files to `results/`, named by start time and run name:

- `<time>-<name>.md`: a summary with the headline metrics (95% confidence intervals),
  a per-category table, and the questions worth reviewing, each with the id of its
  run in the AI inspector.
- `<time>-<name>.json`: everything needed to reproduce and compare the run: the
  dataset and its hash, the document hashes, every retrieval and model setting, the
  model version, package versions, the git commit, and the outcome of each question.

Runs are made with `uv run python -m app.cli evaluate` from `backend/`; see
`datasets/README.md` for the dataset and the options.

Compare runs only when the dataset hash matches. A run made with uncommitted changes
says so in its summary.
