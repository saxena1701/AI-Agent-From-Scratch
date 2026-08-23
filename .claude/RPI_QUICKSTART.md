# RPI Framework Quick Start

This framework provides Claude Code slash commands and sub-agents for structured development workflows.

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/research_codebase` | Deep codebase research with parallel agents |
| `/create_plan` | Create detailed implementation plans |
| `/iterate_plan` | Modify existing plans |
| `/implement_plan` | Execute plans phase by phase |
| `/validate_plan` | Verify implementation matches plan |
| `/create_handoff` | Save context for session handoff |
| `/resume_handoff` | Resume from a saved handoff |
| `/commit` | Create a well-formatted commit |
| `/describe_pr` | Generate PR description |

## Sub-Agents

| Agent | Purpose |
|-------|---------|
| `@codebase-analyzer` | Analyzes implementation details and traces data flow |
| `@codebase-locator` | Finds where files and components live |
| `@codebase-pattern-finder` | Locates similar implementations as templates |
| `@thoughts-analyzer` | Extracts insights from thoughts/ documents |
| `@thoughts-locator` | Discovers relevant documents in thoughts/ |
| `@web-search-researcher` | Expert web research specialist |

## Directory Structure

```
.claude/
├── commands/    # Slash command definitions
└── agents/      # Sub-agent definitions

thoughts/
└── shared/
    ├── research/    # Research findings
    ├── plans/       # Implementation plans
    ├── handoffs/    # Handoff documents
    └── tickets/     # Ticket/task files
```

## Typical Workflow

```bash
# 1. Research before coding
/research_codebase
> How does the authentication system work?

# 2. Create a plan
/create_plan
> Add OAuth2 integration

# 3. Implement the plan
/implement_plan thoughts/shared/plans/2025-01-15-oauth2.md

# 4. Validate it worked
/validate_plan

# 5. Commit your changes
/commit

# 6. Create PR description
/describe_pr

# 7. Save progress if stopping mid-work
/create_handoff

# 8. Resume later
/resume_handoff thoughts/shared/handoffs/...
```

## Customization

Edit command files in `.claude/commands/` to match your project's tooling.

## Source

Commands adapted from: https://github.com/humanlayer/humanlayer/tree/main/.claude
