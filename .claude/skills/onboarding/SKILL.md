---
name: onboarding
description: Onboard a new team member by cloning a Linear ticket template, inviting to Slack channels, adding to recurring calendar meetings, appending to a Notion directory page, and sending a welcome DM. All specifics are config-driven. Invoke with `/onboarding <email> [name] <group>` to run, or `/onboarding init` to bootstrap a fresh config.
---

# Onboarding

Onboard a new team member in one pass. Every step is independent and fails softly — collect errors, continue, aggregate them in the final report.

Config lives at `.claude/skills/onboarding/config.json` (gitignored). The shape is documented in `config.example.json`.

## Inputs

- `email` — required. The new hire's email.
- `name` — optional. Full name ("First Last"). If absent, derive from email local-part: split on `.`, `_`, `-`; title-case each part; join with a space.
- `group` — required. One of the keys in `config.groups` (case-insensitive).

If the operator invokes the skill without arguments or with `init`, run the init subflow below instead of the main flow.

## Subcommand: `init`

Bootstrap `config.json` interactively. Use this when the file is missing or the operator asks to rebuild it. Refuse to overwrite an existing `config.json` unless the operator explicitly confirms.

1. Ask the operator, one question at a time:
   - Linear team name or UUID.
   - Linear parent ticket identifier to use as the template (e.g. `ABC-123`).
   - Notion directory page URL.
   - Slack channel names (one per line, blank ends input).
   - Calendar common meeting titles (one per line, blank ends input).
   - Groups: for each group, ask alias, display name, emoji, retro title (optional), planning title (optional). Blank alias ends input.
   - Welcome message template (paste; end with a line containing only `END`).
2. Fetch parent + children via Linear MCP `get_issue` then `list_issues` with `parentId=<parent>`. For each: copy `title`, `priority.value`, `estimate.value`, `labels`, `description`. Replace the hire's first name in the parent title with `{name}` (ask operator to confirm the name to replace).
3. Fetch the Notion page via `notion-fetch`. Inspect the first group section to detect:
   - `group_heading_format` — typically `### {emoji} {display_name}`.
   - `member_line_prefix` — typically `**Team:** `.
   Show detected patterns to operator; confirm before saving.
4. Write `.claude/skills/onboarding/config.json`.

## Main flow

**1. Load config.**
Read `.claude/skills/onboarding/config.json`. If missing, tell the operator to run `/onboarding init` and stop. On JSON parse error, show the error and stop.

**2. Normalize inputs.**
- If `name` is absent, derive it from `email` local-part.
- `first_name = name.split()[0]`.
- Lowercase `group`.
- If `group` is not a key in `config.groups`, note this — the Notion update and any group-specific meetings will fail for this run but the rest of the flow still runs.

**3. Resolve identities.**
- **Linear user**: `list_users` filtering by `email`. Fall back to searching by name. Record `linear_user_id` or log "Linear user not found for <email>".
- **Slack user**:
  - Read the token: `grep -E "^${config.slack.token_env}=" <repo-root>/${config.slack.token_env_file} | head -n1 | cut -d= -f2-`. Strip surrounding quotes if any. Never log the token.
  - Call `curl -sS -H "Authorization: Bearer $TOKEN" "https://slack.com/api/users.lookupByEmail?email=$(printf '%s' "$EMAIL" | jq -sRr @uri)"`.
  - If `.ok == true`, capture `.user.id`. Else log `.error`.

**4. Linear clone.**
- Get current cycle: MCP `list_cycles` with `teamId=config.linear.team_id`, `type=current`. Capture `cycle.id`. If no current cycle, log and use no cycle.
- Create parent via MCP `save_issue` (create mode — no `id`):
  - `teamId` = `config.linear.team_id`
  - `title` = `config.linear.template.parent.title` with `{name}` replaced by `first_name` (the template reads `[Parent] {name}'s Onboarding`).
  - `priority` = `config.linear.template.parent.priority`
  - `labels` = `config.linear.template.parent.labels` (pass label names; MCP resolves)
  - `assigneeId` = `linear_user_id` (if resolved; else omit)
  - `cycleId` = current cycle id (if resolved)
  - **Do not** set `parentId` — the cloned parent is a top-level parent.
- For each child in `config.linear.template.children`: create via `save_issue` with `parentId = <new parent id>`, same `teamId`, same `assigneeId`, same `cycleId`, and the child's `title`, `estimate`, `priority`, `labels`, `description`. Descriptions are copied verbatim — do not reinterpret markdown.
- Capture the new parent's URL for the welcome DM.

**5. Slack channel invites.**
- Resolve channel IDs: `curl -sS -H "Authorization: Bearer $TOKEN" "https://slack.com/api/conversations.list?limit=1000&types=public_channel,private_channel"`. Build a `name → id` map. Paginate via `response_metadata.next_cursor` if needed.
- For each channel in `config.slack.channels`:
  - If not in the map, log `channel-not-found`.
  - Else `curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data '{"channel":"<id>","users":"<slack_user_id>"}' https://slack.com/api/conversations.invite`.
  - Record `.ok` or `.error` (common: `not_in_channel`, `already_in_channel`, `cant_invite_self`).

**6. Calendar meeting invites.**
- Build the list: `config.calendar.common_meetings` + `config.groups[group].meetings` values (if `group` in config).
- For each title:
  - If title in `config.calendar.event_id_cache`, use that event id.
  - Else use MCP `list_events` on the primary calendar, filter by exact title (case-insensitive). If one match, use it; if multiple, pick the nearest upcoming instance and take its series (`recurringEventId` if present, else `id`). Add to cache.
  - If no match, log `event-not-found` and skip.
- For each resolved event id:
  - MCP `get_event` to get current `attendees`.
  - If `email` is already in attendees, skip (log `already-invited`).
  - Else MCP `update_event` with `attendees = existing + [{email}]`. Preserve all other fields.
- After the loop, persist the updated `event_id_cache` to `config.json` (read the file, merge cache, write back — pretty-print, 2-space indent).

**7. Notion update.**
- If `group` not in `config.groups`, log `group-not-in-config`, skip.
- MCP `notion-fetch` on `config.notion.directory.page_id`.
- Construct the target heading: `config.notion.directory.group_heading_format.format(emoji=config.groups[group].emoji, display_name=config.groups[group].display_name)`.
- Find that heading in the page body. If not found, log `heading-not-found`, skip.
- Starting from the heading, find the first subsequent line that starts with `config.notion.directory.member_line_prefix`. If not found, log `member-line-not-found`, skip.
- Append `, <first_name>` to that line (add a single leading ", "). Use full `name` instead of first name if the existing entries on that line look like full names — otherwise just first.
- MCP `notion-update-page` to write the modified body back. Change only that one line.

**8. Welcome DM.**
- If `slack_user_id` is unresolved, log `slack-user-unresolved`, skip.
- Format: `config.slack.welcome_template` with `{first_name}` and `{linear_url}` (parent URL from step 4). If the Linear parent wasn't created, use the string `<linear-ticket-not-created>`.
- `curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data "$(jq -n --arg ch "$SLACK_USER_ID" --arg txt "$MESSAGE" '{channel:$ch,text:$txt}')" https://slack.com/api/chat.postMessage`.
- Record `.ok` or `.error`.

**9. Report.**
Emit a plain-text report to the user. Shape:

```
Onboarding report for <name> <email> · group: <group>
──────────────────────────────────────────────────
Linear:    parent + N children → <parent_url>
Slack:     M/N channels invited
           failed: [<channel>: <reason>, ...]
Calendar:  X/Y events updated
           failed: [<title>: <reason>, ...]
Notion:    team line updated under "<display_name>"
           (or: skipped — <reason>)
Welcome:   DM sent
           (or: skipped — <reason>)
```

Do not include the Slack token or any secrets in the report.

## Gotchas

- `users.lookupByEmail` returns `users_not_found` if the hire isn't in Slack yet. Expected failure; report and move on.
- `conversations.invite` returns `not_in_channel` if the bot isn't a member of that channel. Operator has to invite the bot once, then re-run that channel manually.
- Skip Reclaim and other attachments from the template parent — descriptions only.
- Calendar `update_event` replaces the `attendees` array. Always read current attendees first and merge.
- Notion `update_page` rewrites the page body. Ensure only the one member line changes; leave all other content untouched.
- No idempotency on Linear. Re-running with the same email creates duplicate tickets.
- Email-to-URL encoding: use `jq -sRr @uri` rather than hand-encoding.
- The Slack token is a user-owned credential even though it's named `SLACK_BOT_TOKEN`. Read it, use it, never print it.
