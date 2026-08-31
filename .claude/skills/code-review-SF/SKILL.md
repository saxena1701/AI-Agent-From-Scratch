---
name: code-review-sf
description: Review code changes for security, performance, correctness, and production readiness, and write the review to a versioned file under thoughts/shared/reviews/. Trigger with a PR URL or diff, "review this before I merge", "is this code safe?", "is this ready to ship?", when checking a change for N+1 queries, injection risks, missing edge cases, error handling gaps, missing timeouts, or an undefined rollback path, or when re-reviewing code after fixes.
argument-hint: "<PR URL, diff, or file path>"
---

# /code-review

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](../../CONNECTORS.md).


Review code changes with a structured lens on security, performance, correctness, production readiness, maintainability, and Stanford-specific best practices.

---

## Stanford Engineering Best Practices

**Security & Secrets**
- Never commit hardcoded secrets or credentials — use Vault or Kubernetes secrets.
- Manage credentials dynamically: retrieve from a secret manager, use, then delete.
- Isolate sensitive files outside your workspace to prevent accidental leaks.
- Configure content exclusion for AI/code tools to block indexing of sensitive files.
- Remember: exclusion rules have gaps — terminal output can leak secrets if not careful.

**DevOps & Environment**
- Check for debug/test flags left enabled — these must not ship to production.
- Review environment variables for unsafe defaults.
- Minimize Docker layers: chain commands in a single RUN.

**Production Hardening**
- Set an explicit timeout on every network and database call — no unbounded waits.
- Retry only idempotent operations, with exponential backoff and jitter.
- Give every external dependency a fallback or circuit breaker; degrade, don't cascade.
- Emit structured logs and metrics on new failure paths — never log PII or secrets.
- Bound resource use: memory limits, connection pool size, page size, batch size.
- Handle SIGTERM: drain in-flight work, close connections, exit clean.
- Ship risky changes behind a feature flag with a documented rollback path.
- Keep migrations backward-compatible (expand, then contract) so rollback actually works.

**AI/Automation Usage**
- Always point the AI at existing files first so new code matches project style and conventions.
- Write requirements in a single file for easier review and context.
- Generate a plan, review it, then build — do not skip straight to code.
- Test AI output manually, especially for data transformations and API parsing.
- AI can produce wrong results — always verify before shipping.
- Trace every non-trivial computed value — the return of *any* function, method, or class call, not just API/model calls — from creation to its actual consumer. A plain helper, validator, or parser whose result is dropped, reassigned and never read, or only printed/logged is the same defect as an unused model-call result, just without the price tag attached. AI-generated code reliably produces steps that run cleanly but whose output goes nowhere; that's not a crash, so it hides from a bugs-only read. If a doc (README/CLAUDE.md) or the function's own name/docstring claims its output drives behavior, verify the call site actually branches on it before taking the claim at face value.

**Process**
- Start by summarizing what the code does before jumping to suggestions.
- Be the checkpoint: plan, review, then execute. You are the last line of defense before anything runs.

---


## Usage

```
/code-review <PR URL or file path>
```

1. Review the provided code changes: @$1
2. If no specific file or URL is provided, ask what to review.
3. Start with a summary of what the code does and its context.
4. Apply the Stanford best practices checklist (above) in addition to standard review dimensions.
5. For each new or changed non-trivial call (especially a model/API/classifier call), trace its return value forward to confirm something actually consumes it — a branch, a prompt, a stored decision. Flag any whose output only reaches a `print`/log/discard as a **computed-but-unused output** finding, even though nothing errors.
6. Call out anything that would page someone at 3am, and say how it gets rolled back.
7. **Write the review to a versioned file under `thoughts/shared/reviews/`** — see [Review Artifacts](#review-artifacts) below. Every review gets a file, no exceptions; then summarize the findings in the terminal and give the path.


## Review Artifacts

Every review is persisted so findings survive the terminal scrollback and can be diffed against the next review of the same target.

**Location**: `thoughts/shared/reviews/` (create it if missing; it sits alongside `plans/`, `research/`, `tickets/`, and `handoffs/` and follows the same conventions).

**Filename**: `YYYY-MM-DD-<target-slug>-v<N>.md`

- `<target-slug>` identifies what was reviewed, kebab-cased and stable across re-reviews of the same thing — e.g. `main-branch-full-review`, `pr-42-query-rewriter`, `src-tool-executor`.
- `<N>` is the version. **Before writing, glob `thoughts/shared/reviews/*<target-slug>*` — if any exist, use the highest N + 1 and set `supersedes` in the frontmatter to that filename.** Never overwrite a prior review; a re-review of the same code is a new version, so the history of what was flagged and what got fixed stays readable.

**Frontmatter** (required, matches the `thoughts/shared/` house style):

```yaml
---
date: <ISO-8601 with offset>
reviewer: Claude
git_commit: <full SHA of the reviewed commit>
branch: <branch name>
repository: <repo name>
target: "<what was reviewed, in words>"
version: <N>
supersedes: <prior filename, or null>
verdict: Approve | Request Changes | Needs Discussion
critical_count: <int>
suggestion_count: <int>
tags: [code-review, <dimension tags>]
status: complete
last_updated: <YYYY-MM-DD>
last_updated_by: Claude
---
```

The body is the Output template below, verbatim, with a repeated header block (Date / Reviewer / Git Commit / Branch / Repository / Verdict) under the title so the file reads standalone.

**Re-review flow**: when asked to re-review something that already has a review on file, read the highest-version file first, then note in the new version which prior findings are fixed, which persist, and which are new.


## How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CODE REVIEW                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  STANDALONE (always works)                                                  │
│  ✓ Paste a diff, PR URL, or point to files                                  │
│  ✓ Start with a summary of what the code does and its context               │
│  ✓ Security audit (OWASP top 10, injection, auth, Stanford secrets policy)  │
│  ✓ Performance review (N+1, memory leaks, complexity, Docker layers)        │
│  ✓ Correctness (edge cases, error handling, race conditions)                │
│  ✓ Usage tracing (a call's output must reach a real consumer, not a log)    │
│  ✓ Production readiness (timeouts, retries, limits, rollback path)          │
│  ✓ Observability (structured logs, metrics, alerts, no PII in logs)         │
│  ✓ Style (naming, structure, readability, matches project conventions)      │
│  ✓ Actionable suggestions with code examples                                │
│  ✓ Check for debug/test flags, unsafe env defaults, and AI output errors    │
│  ✓ Every review saved to thoughts/shared/reviews/ as a versioned file       │
├─────────────────────────────────────────────────────────────────────────────┤
│  SUPERCHARGED (when you connect your tools)                                 │
│  + Source control: Pull PR diff automatically                               │
│  + Project tracker: Link findings to tickets                                │
│  + Knowledge base: Check changes against team coding standards              │
│  + Monitoring: Confirm new failure paths have dashboards and alerts         │
└─────────────────────────────────────────────────────────────────────────────┘
```


## Review Dimensions (with Stanford Best Practices)

### Security
- SQL injection, XSS, CSRF
- Authentication and authorization flaws
- **No secrets or credentials in code** (use Vault/Kubernetes secrets)
- Insecure deserialization
- Path traversal
- SSRF
- **No debug/test flags left enabled**
- **Environment variables have safe defaults**
- **Sensitive files are isolated and excluded from AI tools**

### Performance
- N+1 queries
- Unnecessary memory allocations
- Algorithmic complexity (O(n²) in hot paths)
- Missing database indexes
- Unbounded queries or loops
- Resource leaks
- **Minimized Docker layers (chained RUN commands)**

### Correctness
- Edge cases (empty input, null, overflow)
- Race conditions and concurrency issues
- Error handling and propagation
- Off-by-one errors
- Type safety
- **AI-generated code is manually verified**
- **Computed-but-unused output** — for each new or changed call, of *any* kind (a plain function, a method, a classifier, a model/API call), confirm its return value is actually consumed by a branch, prompt, or downstream decision, not just discarded, reassigned-and-never-read, printed, or logged. A call that runs successfully every time but changes nothing is a usage bug, not a passing test — most costly (and most common) on paid API/model calls, but the same defect on a plain helper. Cross-check against any doc (README/CLAUDE.md) or the function's own name that claims the output drives behavior.

### Production Readiness
- **Timeouts on every external call**; retries backed off, jittered, idempotent
- **Graceful degradation** — fallback or circuit breaker per dependency
- **Structured logs and metrics on new failure paths**, with no PII or secrets
- **Bounded resources** (memory, pools, page/batch size) and clean SIGTERM shutdown
- **Feature-flagged rollout with a documented rollback path**
- **Backward-compatible migrations** (expand, then contract)

### Maintainability
- Naming clarity
- Single responsibility
- Duplication
- Test coverage
- Documentation for non-obvious logic
- **Matches project style, auth patterns, and conventions**


## Output

This template is the body of the review file (written under `thoughts/shared/reviews/` with the frontmatter from [Review Artifacts](#review-artifacts)). In the terminal, give the same content — or a condensed form of it for a large review — plus the path to the file that was written.

```markdown
## Code Review: [PR title or file]

### What This Code Does
[Brief summary of the code’s purpose, context, and any relevant background.]

### Summary
[1-2 sentence overview of the changes and overall quality.]

### Critical Issues
| # | File | Line | Issue | Severity |
|---|------|------|-------|----------|
| 1 | [file] | [line] | [description] | 🔴 Critical |

### Suggestions
| # | File | Line | Suggestion | Category |
|---|------|------|------------|----------|
| 1 | [file] | [line] | [description] | Performance |

### Stanford Best Practices Checklist
- [ ] No hardcoded secrets or credentials
- [ ] No debug/test flags left enabled
- [ ] Environment variables have safe defaults
- [ ] Docker layers minimized
- [ ] Sensitive files isolated/excluded
- [ ] AI-generated code manually verified
- [ ] Matches project style/conventions
- [ ] Every non-trivial computed value (esp. model/API call results) is traced to a real consumer, not just logged/printed

### Production Hardening Checklist
- [ ] Timeouts set on all network/database calls
- [ ] Retries idempotent, backed off, and jittered
- [ ] Failures degrade gracefully instead of cascading
- [ ] New failure paths logged, measured, and alertable — no PII in logs
- [ ] Resource use bounded; SIGTERM handled cleanly
- [ ] Rollout gated by a flag; rollback path documented
- [ ] Migrations backward-compatible

### What Looks Good
- [Positive observations]

### Verdict
[Approve / Request Changes / Needs Discussion]
```

## If Connectors Available

If **~~source control** is connected:
- Pull the PR diff automatically from the URL
- Check CI status and test results

If **~~project tracker** is connected:
- Link findings to related tickets
- Verify the PR addresses the stated requirements

If **~~knowledge base** is connected:
- Check changes against team coding standards and style guides

If **~~monitoring** is connected:
- Confirm new failure paths have dashboards and alerts
- Check current error rates and latency for the affected service


## Tips

1. **Provide context** — "This is a hot path" or "This handles PII" helps me focus.
2. **Specify concerns** — "Focus on security" narrows the review.
3. **Include tests** — I'll check test coverage and quality too.
4. **Write requirements in a single file** for easier review and context.
5. **Generate a plan, review, then build** — don’t skip straight to code.
6. **Test AI output manually** and verify before shipping.
7. **Name the blast radius** — "this runs on every request" changes which hardening matters.
8. **Be the checkpoint** — plan, review, then execute. You are the last line of defense before anything runs.
9. **Ask for a re-review after fixing** — the new version diffs against the last one in `thoughts/shared/reviews/`, so you can see what closed and what's still open.