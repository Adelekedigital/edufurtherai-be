# AGENTS.md - EduFurther AI Router

Guidance for coding agents and reviewers working in this repository. The
GitHub code reviewer (`.github/workflows/code-reviewer.yml`) reads this file
from the base branch, never from the pull request under review.

## Code Review Rules

Flag what would let a caller do or see what it should not, spend budget it
should not, hand a caller output that is not its task's declared shape, or break
the published API contract. Judge reachability with any value the configuration
or the API permits, not only the defaults, and state the input that triggers it.

### Severity

- **P0, critical: must fix.** Bypasses service authentication or task
  authorization, lets one product reach another product's task, leaks a provider
  key, a private key or a caller's payload, or returns a `verified` claim.
- **P1, major: must fix.** Breaks the execute contract or a caller's flow,
  returns `completed` for output that is not the task's declared shape, strands
  an idempotency key, spends past a configured budget, or fails under a value
  the configuration or API permits.
- **P2, minor: fix if cheap.** A real defect with a narrow trigger, or a
  published description (README, docs, a docstring) that contradicts the code.
  Not required to merge.

### Do not flag

CI runs `ruff check` and the full test suite on every push. Do not comment on
style, naming, import order, docstrings, formatting, or anything those tools
decide. Never suggest lowering a threshold, adding an ignore, or raising a limit
to make a check pass.

### How to write a finding

Each finding is read by the author once and acted on. Spend words only on what
changes the fix.

- Plain text. No emoji, no em dashes, no badges or icons, no praise, no greeting
  or closing line.
- Title: the defect in one line, stated as fact, not advice ("Consider ...").
- Body: at most three sentences: the triggering input or state, the wrong
  result, and the safe path. Name the file and symbol; do not restate the diff.
- One finding per root cause. If the same defect appears in several places,
  report it once and list the other locations.
- Only code this pull request changes, or behaviour it changes. Nothing
  speculative: if you cannot name the input that triggers it, leave it out.
- No findings at all is a valid review. Do not pad it with minor points.

### Authentication and authorization

- **The router only verifies.** It never generates, holds or transmits a
  caller's private key. Flag any private key material, or code that signs
  tokens, outside `scripts/` test tooling.
- **A caller with an empty `keys` map authenticates nothing.** Flag a path that
  lets a request through `authenticate()` without a signature checked against
  that product's own registered key, `sub`, `iss`, `aud`, scopes and a fresh
  `jti`.
- **Tasks are authorized per product.** Every `Policy` names its
  `allowed_products`, and the execute endpoint refuses any other product with
  `TASK_NOT_ALLOWED`. Flag a policy that widens a task to another product
  without saying so in the PR, or a check that runs after work is done.
- **A caller never supplies a prompt, schema or model.** `ExecuteRequest` is
  `extra="forbid"`. Flag a new field that lets source data choose behaviour the
  policy should own.
- The development bypass (`ALLOW_UNAUTHENTICATED_DEVELOPMENT`) works only when
  `ENVIRONMENT=development`. Flag anything that loosens that.

### Task registry

- **Adding a task is four edits:** the `Task` enum, its `POLICIES` entry, its
  validator in `_VALIDATORS`, and its `SYSTEM_PROMPTS` entry, plus a
  `PROMPT_VERSIONS` entry. Validators default to deny; flag a task that would
  fall through to another task's check.
- **No model output is ever `verified`.** Every prompt carries
  `NEVER_VERIFIES`, and `validate_candidate` rejects any `verified` key. Flag a
  prompt or validator path that drops either half.
- **A validator is fed raw model output** and must accept any shape without
  raising. Flag a membership test on an unhashable value, an index without a
  type check, or anything else that can raise out of `validate_candidate` or
  `normalize_output`.
- **Change a prompt's text, bump its version.** Flag a `SYSTEM_PROMPTS` change
  that could change output with no matching `PROMPT_VERSIONS` bump on a task
  already in use. Callers store the version as provenance.
- **Size limits are per task**, measured by `source_data_bytes` (the JSON the
  provider receives), and output ceilings are per task too. A reply cut off at
  `max_output_tokens` is unrecoverable on retry; flag a ceiling the task's
  declared output cannot fit inside.
- Profile-writing tasks (`bio_draft`, `bio_improve`, and later "Why I mentor")
  never return an em dash or an emoji: the normalizer repairs them and the
  validator re-checks. Flag a new profile-writing task registered without both.

### Contracts that must hold on retry and under configuration

- **An `Idempotency-Key` retry replays the stored first answer.** Flag
  validation that depends on mutable state (settings, time, another row) and
  runs before the replay lookup, because a retry then fails where the original
  succeeded.
- **An exception escaping the execute loop strands the key** at
  `REQUEST_IN_PROGRESS`, where no retry can replace it. Flag any new call in that
  loop that can raise something other than `ProviderError`.
- **Every setting in `core/config.py` can take any value its type permits.**
  Flag code that assumes a default, such as a budget or rate limit that is
  always set.
- **`ExecuteResponse` is a published contract.** Removing or renaming a field,
  or changing its type or nullability, breaks callers and stored replays; new
  fields are optional so responses stored before the deploy still replay.
- **Provider failures keep their category.** Truncated, empty and invalid
  output are not `provider_unavailable`; flag a change that re-wraps a
  categorised `ProviderError`.

### Secrets and data

- Provider keys and Langfuse keys never reach a log line, an exception message,
  a trace, or a response body. Caller payloads may hold a person's own text;
  never log them whole.
- `SERVICE_CALLERS` holds public keys only. Flag a private key in it, in
  `.env.example`, or anywhere in git.

### Migrations (`alembic/`)

- The application never migrates on startup; migrations run explicitly.
- **Schema changes are expand/contract across releases.** Flag a single
  migration that drops, renames or tightens something the deployed code still
  reads or writes.
- Raw SQL is parameterised. Formatting values into SQL is an injection bug.

### Tests

- Flag a test that cannot fail for the reason it names: an authorization test
  that never reaches the policy check, a security property asserted only on the
  success path, or a fixture that cannot tell two cases apart.
