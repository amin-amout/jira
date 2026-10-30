# Jira skill

A config-driven Jira integration for Claude (Claude Code / Desktop), GitHub Copilot (VS Code) and OpenAI Codex (CLI). Python 3 standard library only.

The engine (`jira.py`) knows no Jira operation by name. Operations, payload templates, field mappings, defaults and per-project overrides are all data in one file, `jira-agent.config.yml`. Add or change an operation by editing YAML.

## Layout

| Path | Purpose |
|---|---|
| `.claude/skills/jira/SKILL.md` | Skill definition and full reference (auto-discovered by Claude) |
| `.claude/skills/jira/jira-agent.config.yml` | Operation registry, templates, defaults, per-project overrides |
| `.claude/skills/jira/jira.py` | Generic engine |
| `AGENTS.md`, `.github/copilot-instructions.md` | Codex / Copilot entry points (point to the skill) |

Copy `jira-agent.config.yml` to your repo root to customise it per repository; it takes precedence over the bundled one.

## Setup

```bash
export JIRA_BASE_URL="https://jira.example.com"
export JIRA_PAT="<personal access token>"     # JIRA_TOKEN also works
export JIRA_PROJECT_KEY="PROJ"                # optional default project
```

Server/Data Center uses a Bearer PAT with API v2. Cloud: also set `JIRA_EMAIL` (Basic auth) and optionally `JIRA_API_VERSION=3`.

## Usage

```bash
python3 .claude/skills/jira/jira.py ops                       # list operations
python3 .claude/skills/jira/jira.py ops create                # required / optional values
python3 .claude/skills/jira/jira.py create                    # title + description from the latest commit
python3 .claude/skills/jira/jira.py add-comment issue=PROJ-1 body="Fixed"
python3 .claude/skills/jira/jira.py update issue=PROJ-1 priority=High labels=a,b duration=2h
python3 .claude/skills/jira/jira.py transition issue=PROJ-1 transition_id=31
python3 .claude/skills/jira/jira.py create --dry-run          # print the request, send nothing
```

Or ask your agent: "create a Jira ticket for this change". See [SKILL.md](.claude/skills/jira/SKILL.md) for the config reference.
