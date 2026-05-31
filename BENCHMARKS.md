# Benchmarks

## Token savings: libbie-pack-git vs raw git porcelain

The whole pitch is: deterministic dispatch with no LLM in the hot path saves
your context budget. Putting a number on it.

### Setup

- Repo under test: pallets/flask @ HEAD, depth-200 clone (677 commits visible to git log).
- Working tree: modified src/flask/app.py + src/flask/cli.py, untracked new_file_for_bench.md. One file staged. Realistic mid-task agent scenario.
- Tokenizer: tiktoken cl100k_base (GPT-4 / Claude approximation).
- Comparison: raw git porcelain output vs. libbie-pack-git v0.2.0 handler output, byte-for-byte.

### Results (2026-05-31)

| Scenario                                            | Raw tokens | JSON tokens | Saved  |    %    |
|-----------------------------------------------------|-----------:|------------:|-------:|--------:|
| Repo overview (status + log -10 + branch + remote)  |        263 |         398 |   -135 |  -51.3% |
| Recent activity (log -30)                           |      3,995 |         855 |  3,140 |  +78.6% |
| Change inspection (status + diff + diff --staged)   |        170 |         120 |     50 |  +29.4% |
| Commit detail (git show HEAD~5)                     |         96 |          61 |     35 |  +36.5% |
| **TOTAL across 4 scenarios**                        |  **4,524** |   **1,434** |**3,090**|**+68.3%**|

### What this means

The big win is on inspection-heavy operations (log -N, show, diff) where raw
git emits a lot of redundant porcelain: repeated authors, full 40-char SHAs,
separator lines. We strip to short SHAs, drop redundant fields, and emit
compact JSON. log -30 alone saves 3,140 tokens on this one repo.

The "Repo overview" scenario is a net loss because raw git status on a small
working tree is already terse: there is not much to compress. For tiny repos
the savings disappear. For real codebases mid-task, the savings scale
linearly with how much history you ask for.

### Reproduce on your own repo

The benchmark script is shipped in the Pack bundle as benchmark.py. Run it
against any repo you have lying around:

    pip install libbieai-swiszard
    pip install libbie_pack_git-0.2.0-py3-none-any.whl
    pip install tiktoken
    python benchmark.py /path/to/your/repo

We invite anyone to run it against their own work and post their numbers.
The script is 100 lines, no magic, no LLM calls.

### What is NOT measured here

- Latency. Both paths shell out to git; libbie-pack-git adds ~1 ms of Python
  parsing. Imperceptible.
- Round-trip cost. A full LLM round-trip costs the same per token regardless.
  Savings show up in monthly bills and context-window utilization, not in any
  single requests wall-clock.
- Other handlers. This is the git Pack only. Future Packs (docker, k8s,
  github-api) will publish their own benchmarks against their own porcelain.
