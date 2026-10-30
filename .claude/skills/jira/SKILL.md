---
name: jira
description: Create Jira tickets, update a ticket's description or other fields, and add comments via the Jira REST API. Use when the user asks to create/open/file a Jira ticket or issue, update/change a ticket description, edit ticket fields (priority, assignee, labels, estimate), or comment on a Jira issue. Title and description are auto-filled from the current Git commit.
compatibility: Needs python3 (standard library only) and network access to your Jira. Optional git for auto-fill. Works in Claude Code / Claude Desktop (skill), GitHub Copilot in VS Code and OpenAI Codex CLI (via AGENTS.md / copilot-instructions.md pointing here).
---

# Jira

All operations go through one script: `.claude/skills/jira/jira.py` (run from the repo root).
Each operation is driven by a YAML template in `.claude/skills/jira/templates/`.

## Setup (once)

```bash
export JIRA_BASE_URL="https://jira.example.com"   # no trailing slash; Cloud: https://your-domain.atlassian.net
export JIRA_PAT="<personal access token>"          # JIRA_TOKEN is accepted as an alias
```

Never write the token into files or commit it.

| Jira flavour | Auth | Extra env | API |
|---|---|---|---|
| Server / Data Center (PAT) | `Authorization: Bearer $JIRA_PAT` | none | v2 (default). `/rest/api/3` does not exist on Data Center |
| Cloud (API token) | `Authorization: Basic base64(email:token)` | `JIRA_EMAIL=you@example.com` | v2 (default) or `JIRA_API_VERSION=3` |

`JIRA_API_VERSION=3` switches every endpoint to `/rest/api/3` and wraps text in Atlassian Document Format (required by v3 for descriptions/comments). Default is `2` because it accepts plain-text bodies and works on both Server/DC and Cloud. Bearer PATs are not accepted by Jira Cloud.

## Actions

| User intent (natural language) | Command | HTTP |
|---|---|---|
| "create a Jira ticket / issue / bug for this" | `create` | `POST /rest/api/{2\|3}/issue` |
| "update the description of PROJ-1" | `update-description` | `PUT /rest/api/{2\|3}/issue/{issueKey}` |
| "comment on PROJ-1", "add a note to PROJ-1" | `add-comment` | `POST /rest/api/{2\|3}/issue/{issueKey}/comment` |
| "set priority/assignee/labels/estimate/title on PROJ-1" | `update` | `PUT /rest/api/{2\|3}/issue/{issueKey}` |

```bash
python3 .claude/skills/jira/jira.py create             --set project=PROJ [--set summary="..."] [--set key=value ...]
python3 .claude/skills/jira/jira.py update-description --set issue=PROJ-1 [--set description="..."]
python3 .claude/skills/jira/jira.py add-comment        --set issue=PROJ-1 [--set body="..."]
python3 .claude/skills/jira/jira.py update             --set issue=PROJ-1 --set priority=High
```

Flags: `--set KEY=VALUE` overrides/adds a template field (repeatable); `--template FILE` uses a different template; `--dry-run` prints method, URL and JSON payload without sending (no credentials needed). Use `--dry-run` first if anything is unclear.

Rules for the agent:
1. Ask the user for anything required that is missing and cannot be inferred: `project` and `issuetype` for create (edit the template defaults `PROJ`/`Task` or pass `--set`), `issue` key for every other command.
2. Do not set optional fields the user did not ask for.
3. Report the script output verbatim (the created key and URL, or the error). On `HTTP 401/403` check the token; on `HTTP 400` the response body names the invalid field.
4. Never print or echo `JIRA_PAT`.

## Auto-fill from Git

Templates may contain placeholders, filled at run time from `git log -1` / `git rev-parse` (the script runs the git commands itself; no manual injection needed):

| Placeholder | Source | Fallback without git/commits |
|---|---|---|
| `{{commit}}` | `git log -1 --pretty=%H` | `no commit hash available` |
| `{{commit_subject}}` | `git log -1 --pretty=%s` | `Manual ticket` |
| `{{branch}}` | `git rev-parse --abbrev-ref HEAD` | `unknown` |
| `{{git_context}}` | `Commit: <hash>` + `Branch:` + `Subject:` lines | `Manual ticket – no commit hash available` |

To use a different text, pass `--set description="..."` / `--set body="..."` (placeholders still work inside it, e.g. `--set 'body=Fixed in {{commit}}'`).

## YAML templates

Minimum fields are active; optional fields are commented out. Supported YAML is a flat subset: `key: value`, quoted strings, `[a, b]` lists, `|` multi-line blocks, full-line `#` comments (no nesting, no escapes).

`templates/create.yaml`
```yaml
project: PROJ
issuetype: Task
summary: "{{commit_subject}}"
description: |
  {{git_context}}
# priority: Medium
# assignee: jdoe              # Server/DC: username. Cloud (JIRA_EMAIL set): accountId
# labels: [backend, urgent]
# components: [API]
# duedate: 2026-12-31         # YYYY-MM-DD
# duration: 2h                # timetracking.originalEstimate, e.g. 1d 4h
```

`templates/update-description.yaml`
```yaml
# issue: PROJ-123             # pass with --set issue=PROJ-123
description: |
  {{git_context}}
```

`templates/add-comment.yaml`
```yaml
# issue: PROJ-123             # pass with --set issue=PROJ-123
body: |
  {{git_context}}
# visibility_role: Administrators
```

`templates/update.yaml` — only listed fields are changed (`summary`, `description`, `priority`, `assignee`, `labels`, `components`, `duedate`, `duration`); `labels`/`components` replace existing values.

## Payloads sent (v2; v3 differs only in text fields being ADF documents)

```json
POST /rest/api/2/issue
{"fields": {"project": {"key": "PROJ"}, "issuetype": {"name": "Task"}, "summary": "...", "description": "...",
            "priority": {"name": "Medium"}, "assignee": {"name": "jdoe"}, "labels": ["a"],
            "components": [{"name": "API"}], "duedate": "2026-12-31",
            "timetracking": {"originalEstimate": "2h"}}}

PUT /rest/api/2/issue/PROJ-1        -> 204 No Content
{"fields": {"description": "..."}}

POST /rest/api/2/issue/PROJ-1/comment
{"body": "...", "visibility": {"type": "role", "value": "Administrators"}}
```
