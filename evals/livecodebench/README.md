# LiveCodeBench (release v6 problems)

175 programming problems from AtCoder and LeetCode contests held between
January and April 2025, each answered `k` times with a Python program that is
then run on the problem's public and hidden tests. From Jain et al.,
"LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language
Models for Code" (arXiv 2403.07974).

- **Data.** `test6.jsonl` of `livecodebench/code_generation_lite` on Hugging
  Face at revision `0fe84c3912ea0c4d4a78037083943e8f0c4dd505`: the problems
  that release v6 added. It is read when a run builds the task and refused
  unless its SHA-256 matches the one in `task.py`. The file is 134 MB, most of
  it hidden tests. The problems are ordered newest contest first, so a study
  that runs fewer of them runs the most recent ones.
- **Hidden tests.** Most are stored as JSON inside a compressed pickle. They
  are read with an unpickler that loads no class and no function, so the file
  cannot run code on the harness.
- **Prompt and grading.** Those of the authors' runner
  (`LiveCodeBench/LiveCodeBench`, commit
  `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`, MIT): its generic system prompt
  and question template, the reply's last fenced block as the program, six
  seconds per test, and its comparison rules. A program that reads stdin must
  print the expected lines (compared after stripping, or as the same numbers);
  a `Solution` method must return the expected value. Testing stops at the
  first test that fails.
- **Per reply** (`score()`): `pass_at_1` (1 when every test passes),
  `tests_run`, `failure` (`wrong_answer`, `runtime_error`, `timeout`, `budget`
  or `sandbox_died`, when the program brought the worker down), `answer_valid` (0 when the reply has no fenced block; such a
  reply fails) and `verifier_status`.
- **Per arm** (`endpoints()`): `pass_at_1`, `pass_at_k` (problems with at
  least one passing reply) and `pass_rate_entropy` (binary entropy of a
  problem's pass rate over its `k` replies), each averaged over problems.

The analysis reads one value per reply, so it tests `pass_at_1`. The card's
headline `pass_rate_entropy` is computed by `endpoints()` for description and
is not part of the registered analysis.

## The sandbox

A reply is a program written by a model. The harness never runs it. `score()`
sends the program and its tests over a Unix socket to a separate service,
which an operator installs once (`docs/SETUP_GUIDE.md`, section 1):

- `infra/qr-llm-evals-sandbox.socket` listens at
  `/run/qr-llm-evals-sandbox/sandbox.sock`. Only the harness's group may
  connect.
- `infra/qr-llm-evals-sandbox@.service` starts `sandbox_worker.py` once per
  connection with the system Python, under a user that exists only for that
  job, with no network, no access to the harness's data, settings or keys, and
  limits on memory, processes and running time. The worker runs the program
  once per test in a child process with its own CPU, memory and output limits.

Where the socket does not exist (a laptop, CI, a box without the units),
replies are generated and stored but not scored: their rows say
`verifier_status: unavailable` and carry no `pass_at_1`, and the run flags
them as unparsed. Nothing falls back to running the program in the harness.

The worker has been tested with small trusted programs. The two units have not
been started on the box yet; check them there before a study relies on this
eval (the setup guide gives the commands).

Scoring happens while the run generates, one reply at a time, and a reply that
passes every test has run about 40 programs. A slow or looping program costs
up to six seconds per test and at most four minutes per reply.

Differences from upstream:

- Upstream runs every test inside one Python process, with the program wrapped
  in a function and stdin patched. Here each test is its own process that
  reads real stdin, which also runs an `if __name__ == "__main__":` block as
  written.
- Programs run on the box's system Python with the standard library only.
  Upstream's environment has NumPy installed.
- The target runs under the study's sampling envelope with `k` replies per
  problem. Upstream samples 10 replies at temperature 0.2.
- Whether the contests are later than a model's training cutoff depends on the
  model; the card does not check it.
