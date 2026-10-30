---
name: jira
description: Create and manage Jira issues through the Jira REST API - create tickets, update the description or other fields, add comments, assign, transition, log work and attach files. Use when the user mentions Jira, a ticket/issue key like PROJ-123, or asks to file, update, comment on, move, assign or close a ticket. Title and description are auto-filled from the current Git commit. Operations, field mappings, defaults and per-project overrides are all defined in jira-agent.config.yml.
compatibility: Needs python3 (standard library only) and network access to Jira. git is optional (used for auto-fill). Claude Code / Claude Desktop read this file as a skill; GitHub Copilot (VS Code) and OpenAI Codex CLI reach it through .github/copilot-instructions.md and AGENTS.md.
---

# Jira

One script, one config file:

- `.claude/skills/jira/jira.py` - generic engine (does not know any Jira operation by name)
- `.claude/skills/jira/jira-agent.config.yml` - the operation registry, payload templates, defaults and per-project overrides

## Setup (environment variables; never write secrets into files)

```bash
export JIRA_BASE_URL="https://jira.example.com"   # Cloud: https://your-domain.atlassian.net
export JIRA_PAT="<personal access token>"          # JIRA_TOKEN also accepted
export JIRA_PROJECT_KEY="PROJ"                     # optional default project
# optional: JIRA_EMAIL (Cloud: Basic auth), JIRA_API_VERSION (2|3), JIRA_USER_KEY (name|accountId)
```

| Jira | Auth | Extra env | Notes |
|---|---|---|---|
| Server / Data Center | `Authorization: Bearer $JIRA_PAT` | none | REST API v2 (default). Data Center has no `/rest/api/3` |
| Cloud | `Authorization: Basic base64(email:token)` | `JIRA_EMAIL`, `JIRA_USER_KEY=accountId` | v2 or v3. Cloud does not accept Bearer PATs |

`JIRA_API_VERSION=3` switches every endpoint to `/rest/api/3` and the `rich` filter turns text into Atlassian Document Format, which v3 requires for descriptions and comments.

## How the agent works

1. **Discover**: run `python3 .claude/skills/jira/jira.py ops` (list) or `... ops <name>` (required/optional values and defaults). The config is the source of truth; operations may have been added or changed.
2. **Run**: `python3 .claude/skills/jira/jira.py <operation> key=value ... [--dry-run]`
3. Ask the user for any value reported as missing; never invent project keys, issue keys or transition ids. Do not pass optional values the user did not ask for.
4. For `transition`, run `list-transitions issue=KEY` first and pick the id that matches the user's wording.
5. Report the script's output verbatim. Never print `JIRA_PAT`. On errors the script prints the HTTP status and Jira's message.

Natural language to operation (built-in set):

| User says | Command |
|---|---|
| "create a ticket / bug for this" | `create` / `create-bug` (title + description from the latest commit unless given) |
| "show PROJ-1" | `get issue=PROJ-1` |
| "update the description of PROJ-1" | `update-description issue=PROJ-1 [description=...]` |
| "set priority/assignee/labels/estimate/title" | `update issue=PROJ-1 priority=High labels=a,b duration=2h` |
| "comment on PROJ-1" | `add-comment issue=PROJ-1 [body=...]` |
| "assign PROJ-1 to bob" | `assign issue=PROJ-1 assignee=bob` |
| "move PROJ-1 to Done" | `list-transitions`, then `transition issue=PROJ-1 transition_id=31 [resolution=Done] [comment=...]` |
| "log 2h on PROJ-1" | `log-work issue=PROJ-1 duration=2h [comment=...]` |
| "attach build.log" | `attach-file issue=PROJ-1 file=build.log` |

Values: `key=value`; `key=@path` reads the value from a file; `key=` (empty) switches a default off (e.g. `description=`). Command-line values are literal text. `--dry-run` prints method, URL and JSON without sending and needs no credentials. `--config FILE` (or `$JIRA_AGENT_CONFIG`) selects another config; otherwise `./jira-agent.config.yml` in the working directory wins over the one next to the script, so a repo can ship its own.

Auto-fill: the script runs `git log -1 --pretty=%H` (and branch, subject, author) in the current directory and exposes `commit_hash`, `commit_short`, `commit_subject`, `branch`, `repo`, `author` and `git_context` to templates. `create` uses `commit_subject` as title and `git_context` (hash, branch, subject) as description. Without a Git repo or commits: title `Manual ticket`, description `Manual ticket – no commit hash available` (both configurable under `git:`).

## Config reference (`jira-agent.config.yml`)

| Section | Purpose |
|---|---|
| `connection` | `base_url`, `token_env` (env var names, first set wins), `user` (set = Basic auth), `api_version`, `api_prefix`, `timeout` |
| `defaults` | Global default values (`project`, `issuetype`, `user_key`, add `priority`, `labels`, ...). Any key here can also be passed as `key=value` |
| `git` | Templates for `git_context` and the no-Git fallbacks |
| `templates` | Reusable payload fragments, composed with `extends` |
| `operations` | The registry: one entry per operation |
| `projects` | Per-project overrides keyed by project key; deep-merged over everything above |

**Environment interpolation**: `${VAR}` and `${VAR:-fallback}` in config values are replaced when the file loads.

**Placeholders**: `{{ name }}` or `{{ name | filter | filter:arg }}`. Dotted lookups work (`{{ response.key }}`, `{{ response.0.filename }}`). Precedence: command line > operation `defaults` > global `defaults` > Git built-ins. Defaults may reference other variables (`description: "{{ git_context }}"`). A whole-value placeholder keeps its type (list, number, ADF object). Placeholders are also rendered in dictionary keys.

**Optional by omission**: any key or list item whose value renders empty is dropped from the request. An optional field is therefore just a placeholder nobody filled in; `{name: "{{ priority }}"}` vanishes entirely when `priority` is unset.

**Filters**: `list` (comma string to list), `rich` (text -> plain on v2, ADF on v3), `number`, `upper`, `lower`, `default:X` (use X if empty), `then:X` (emit X only if the value is set).

**Operation keys**: `about`, `method`, `path` (appended to `api_prefix`; every placeholder in it is implicitly required), `required`, `require_any` (at least one), `defaults` (operation-only), `payload` (JSON body template, may use `extends`), `headers`, `attach` (path of a file to upload as multipart field `file`), `prefix` (replaces `api_prefix`, e.g. `/rest/agile/1.0`), `result` (message template; `response` is the parsed JSON reply), `extends` (inherit another operation).

**Merging**: `extends` and `projects` deep-merge dictionaries; lists and scalars are replaced; a `null` deletes an inherited key (e.g. `labels: null`). `extends` is resolved at the top of an operation or of a `payload`/template.

**YAML supported**: block maps and lists, `|` / `|-` block text (trailing newlines are dropped), `[a, b]` and `{a: b}` inline values, quoted/plain scalars, `#` comments. Always quote strings that contain `{{`, `: ` or start with `[`/`{`. No anchors or tags.

## Customising (YAML only)

New operation - link two issues (`POST /rest/api/2/issueLink`):
```yaml
operations:
  link-issues:
    about: Link two issues, e.g. type=Blocks inward=PROJ-1 outward=PROJ-2.
    method: POST
    path: /issueLink
    required: [type, inward, outward]
    payload:
      type: {name: "{{ type }}"}
      inwardIssue: {key: "{{ inward }}"}
      outwardIssue: {key: "{{ outward }}"}
    result: "Linked {{ inward }} -> {{ outward }}"
```
Variant of an existing operation: `create-task-for-team: {extends: create, defaults: {labels: [team-x], priority: High}}`.

Per-project field ids (custom fields differ per instance; find ids with `GET /rest/api/2/field`):
```yaml
projects:
  ABC:
    defaults: {issuetype: Story}
    templates:
      fields:
        fields:
          customfield_10016: "{{ story_points | number }}"
```
Then `jira.py create project=ABC story_points=5`.

## API reference (v2 shown; v3 differs only in `/rest/api/3` and ADF text)

| Operation | Request |
|---|---|
| create | `POST /rest/api/2/issue` `{"fields": {project, issuetype, summary, description, priority, assignee, labels, components, duedate, timetracking, parent}}` |
| update, update-description | `PUT /rest/api/2/issue/{issueKey}` `{"fields": {...}}` (204) |
| add-comment | `POST /rest/api/2/issue/{issueKey}/comment` `{"body": "...", "visibility": {"type": "role", "value": "..."}}` |
| assign | `PUT /rest/api/2/issue/{issueKey}/assignee` `{"name": "..."}` or `{"accountId": "..."}` (204) |
| list-transitions / transition | `GET` / `POST /rest/api/2/issue/{issueKey}/transitions` `{"transition": {"id": "31"}, "fields": {...}, "update": {"comment": [{"add": {"body": "..."}}]}}` (204) |
| log-work | `POST /rest/api/2/issue/{issueKey}/worklog` `{"timeSpent": "1h 30m", "comment": "..."}` |
| attach-file | `POST /rest/api/2/issue/{issueKey}/attachments`, multipart field `file`, header `X-Atlassian-Token: no-check` |
