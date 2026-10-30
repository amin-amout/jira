# Jira skill

A minimal Jira integration for Claude (Claude Code / Desktop), GitHub Copilot (VS Code) and OpenAI Codex (CLI). It can create tickets, update a ticket's description or other fields, and add comments. Python 3 standard library only, no dependencies.

## Layout

| Path | Purpose |
|---|---|
| `.claude/skills/jira/SKILL.md` | Skill definition (auto-discovered by Claude); full reference |
| `.claude/skills/jira/jira.py` | The script that calls the Jira REST API |
| `.claude/skills/jira/templates/*.yaml` | One YAML payload template per operation |
| `AGENTS.md` | Codex entry point (points to the skill) |
| `.github/copilot-instructions.md` | Copilot entry point (points to the skill) |

## Setup

```bash
export JIRA_BASE_URL="https://jira.example.com"   # no trailing slash
export JIRA_PAT="<personal access token>"          # JIRA_TOKEN also works
```

- **Server / Data Center:** Bearer PAT, API v2 (default).
- **Cloud:** also set `JIRA_EMAIL` (Basic auth). Set `JIRA_API_VERSION=3` to use `/rest/api/3`; the default is `2`.

## Usage

```bash
python3 .claude/skills/jira/jira.py create             --set project=PROJ
python3 .claude/skills/jira/jira.py update-description --set issue=PROJ-1
python3 .claude/skills/jira/jira.py add-comment        --set issue=PROJ-1 --set body="Done"
python3 .claude/skills/jira/jira.py update             --set issue=PROJ-1 --set priority=High
```

Add `--dry-run` to print the request without sending it (no credentials needed). Title and description are auto-filled from the latest Git commit; without a Git repo they fall back to "Manual ticket – no commit hash available".

Or just ask your agent: "create a Jira ticket for this change". See [`SKILL.md`](.claude/skills/jira/SKILL.md) for placeholders, templates and payloads.
