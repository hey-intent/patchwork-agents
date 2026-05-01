# ADR 0002: Agentic Command Flow

## Status

Accepted

## Context

The previous orchestration model used `ai-pr-*` labels for two different
purposes:

- selecting the AI worker provider
- triggering worker execution

That coupling made work easy to start accidentally and made it hard to add
multi-step agent flows such as spec, plan, implement, review, and continue.

The source-provider abstraction already exposes normalized issue, comment,
label, and webhook event concepts. GitHub can parse `issue_comment` webhooks
into `issue_commented` events, which gives the system a source-agnostic place
to detect explicit user commands.

## Decision

Use an explicit command-based agent flow.

Provider labels are declarative configuration only:

```text
ai-pr-aider
ai-pr-codex
ai-pr-claude
```

Issue comments trigger agent actions:

```text
/agent spec
/agent plan
/agent implement
/agent review
/agent continue
```

For the first milestone, only `/agent implement` starts a worker. Other known
actions are recognized but do not create a worker job until their flows exist.

Source providers own source-specific webhook parsing and command extraction.
The orchestrator must not branch on provider-specific webhook names such as
GitHub's `issues` or `issue_comment`.

The orchestrator delegates command flow logic to an `AgentOrchestrator` class
instead of keeping that behavior inside the FastAPI route handler.

Agent status is represented with one source label at a time:

```text
agent:status:idle
agent:status:running
agent:status:awaiting-human
agent:status:blocked
agent:status:failed
agent:status:done
```

## Consequences

### Positive

- Adding or changing provider labels no longer starts work.
- Agent execution is explicit and auditable through issue comments.
- The orchestration route stays small and delegates flow behavior to a focused
  class.
- Source-specific webhook details remain inside source providers.
- Future actions can be added without changing the trigger model.
- Issue state is visible through labels.

### Negative

- A user now needs two steps to start work: add provider label, then comment
  `/agent implement`.
- Worker scripts need source API access to mark terminal states such as
  `done` and `failed`.
- The system needs to keep state labels consistent and avoid accumulating
  multiple `agent:status:*` labels.
- The known action list is duplicated between Python and shell for the MVP.
  Introduce a single generated source of truth when multiple actions become
  executable.

## Implementation Notes

- `SourceProvider.get_action_trigger(event)` returns an `ActionTrigger | None`.
- `SourceProvider.can_trigger_action(event, trigger)` enforces source-specific
  actor authorization. The GitHub provider allows actors with `write`,
  `maintain`, or `admin` repository permission.
- GitHub extracts commands only from `issue_commented` events.
- Unknown commands such as `/agent foo` return no trigger.
- Action triggers store a normalized command such as `/agent implement`, not the
  raw user comment line.
- Known but unimplemented actions set `agent:status:awaiting-human` and comment
  back.
- Missing provider label sets `agent:status:awaiting-human` and comments back.
- Accepted `/agent implement` sets `agent:status:running` and creates a worker
  job.
- Worker jobs receive:

```text
SOURCE_ACTION
SOURCE_TRIGGER
SOURCE_TRIGGER_COMMAND
```

- Worker prompts are dispatched through `prompt/action_prompt.sh`.

## Alternatives Considered

### Keep Label-Based Triggering

Rejected. It preserves accidental execution risk and does not support a clean
multi-action flow.

### Put Command Parsing In The FastAPI Route

Rejected. It would reintroduce source-specific branching into the orchestrator
HTTP layer and make future providers harder to add.

### Put Orchestration Directly In `app.py`

Rejected. The endpoint would become responsible for verification, parsing,
state transitions, provider resolution, worker creation, and user feedback. A
dedicated orchestration class keeps the route thin and the flow testable.

## Related

- ADR 0001: Source Provider Abstraction
- `.local/agentic-flow-prd.md`
- `.local/agentic-flow-plan.md`
