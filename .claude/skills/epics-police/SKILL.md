---
name: epics-police
description: Use when the user says "epics police", "police the epics", "check orphans", "audit epics", "who has no parent", "are we clean", or invokes /epics-police. Read-only weekly audit that enforces "every feature ticket must ladder up to a Linear [EPIC] parent". Dispatches parallel agents to fetch Linear tickets, Notion epics, and people from the Management API, runs deterministic + LLM semantic matching to cluster orphans, and pushes the analysis to the API for the interactive UI.
---

# Epics Police — Framework Enforcement Audit

Read-only weekly audit enforcing: **every feature ticket must ladder up to a Linear ticket whose title starts with `[EPIC]`**. Runs deterministic plus LLM-assisted matching to cluster orphan tickets, cross-references the Notion epics catalog, and pushes the analysis to the Management API. The interactive UI at `http://localhost:8100/epics-police` renders the results with drag-and-drop hierarchy editing and suggestion previews.

## When to Use

- User says `epics police`, `police the epics`, `check orphans`, `audit epics`, `who has no parent`, `are we clean`
- User invokes the `/epics-police` slash command
- User wants to know how well this week's Linear work maps to declared epics
- User wants a list of orphan tickets and proposed missing epics

**Never invoke for:** writing to Linear, creating epics, moving tickets, or any mutation. This skill is strictly read-only analysis. Mutations happen through the interactive UI or the `PATCH /api/v1/linear/tickets` endpoint.

## Scope

- **Single week per run.** Operates on the current week's Linear cycle data from `/api/v1/linear?week=<monday>`.
- **Read-only.** No writes to Linear, Notion, or any remote system. Only writes to the Management API's analysis storage and decision log.
- **Self-improving.** Consumes learned weights and patterns from past accept/reject decisions. Each run may update the tunables block in this skill file if learned weights have diverged from current values.
- **No file I/O** (except self-calibration). All output goes to the Management API via `POST /api/v1/epics-police/analysis`. The only local file write is updating this SKILL.md's tunables block during calibration.

## Configuration

**Management API** (`http://localhost:8100`) — assumed to be pre-populated for the target week. If not reachable, abort with a clear error after one `jura api restart` retry.

| Endpoint | Value | Purpose |
|---|---|---|
| `GET /api/v1/config/linear/team_name` | *(your team)* | Linear team filter (informational) |

### Ignored Assignees Config (Management API)

Stored in the Management API config table at `GET /api/v1/config/epics_police/ignored_assignees`.

| Key | Purpose |
|---|---|
| `ignored_assignees` | List of `{display_name, email, person_id, linear_user_id}` entries. Tickets assigned to anyone in this list are filtered OUT **before Pass 1**. Used to exclude specific people (e.g. PMs, interns, or people whose Linear activity is out-of-scope for the engineering audit) without touching the codebase. |

**Load this config at Step 1** (after API health check): `GET http://localhost:8100/api/v1/config/epics_police/ignored_assignees`. Build a set of ignored `person_id`s from the response's `value` array. If the endpoint returns 404, treat the ignore list as empty and proceed normally.

## Data Sources

All data comes from the Management API. The `week` query parameter is always the ISO date of the week's Monday (e.g. `2026-04-06`), resolved from `/api/v1/weeks` in Step 1 of the workflow.

| Endpoint | Content |
|---|---|
| `GET /api/v1/health` | Liveness check |
| `GET /api/v1/weeks` | Week metadata (`week_label`, `month_dir`, `monday`) |
| `GET /api/v1/linear?week=<YYYY-MM-DD>&limit=5000` | All current-week tickets with parent/child refs, labels, comments |
| `GET /api/v1/epics?week=<YYYY-MM-DD>&limit=5000` | Notion epic catalog (status, team, pm_lead, content, sub_pages) |
| `GET /api/v1/people?limit=5000` | Identity + squad resolution |
| `GET /api/v1/epics-police/learnings` | Distilled learned weights, thresholds, and structural patterns |
| `GET /api/v1/epics-police/analysis` | Previous run's analysis (for implicit decision detection) |
| `POST /api/v1/epics-police/decisions` | Log inferred decisions from comparing previous analysis to current state |
| `POST /api/v1/epics-police/distill` | Trigger re-distillation of learnings from all stored decisions |

## Output

The analysis JSON is pushed to the Management API:

```
POST /api/v1/epics-police/analysis
```

The interactive UI loads it from `GET /api/v1/epics-police/analysis` and renders at `http://localhost:8100/epics-police`.

No local files are written.

## Workflow

### Step 0 — Detect implicit decisions

Before running the analysis, compare the **previous run's analysis** to the **current Linear state** to infer decisions the user made outside the UI (or via Linear directly).

1. Fetch `GET http://localhost:8100/api/v1/epics-police/analysis`. If 404, skip this step (first run).
2. From the previous analysis, collect all `matched_orphans` across `declared_epics` — these are suggestions that were shown to the user.
3. Fetch fresh Linear tickets: `GET http://localhost:8100/api/v1/linear?limit=5000` (current week).
4. For each previous suggestion `{orphan_identifier, suggested_epic_id, confidence, signals, match_source}`:
   - Look up the orphan in the fresh ticket data.
   - **Implicit accept**: orphan's current `parent_identifier` == `suggested_epic_id` → decision = `"accepted"`, inferred = true.
   - **Implicit redirect**: orphan's current `parent_identifier` is set but != `suggested_epic_id` → decision = `"redirected"`, inferred = true, `actual_parent_id` = current parent.
   - **Ignored**: orphan is still unparented → skip (user hasn't acted yet).
   - **Already logged**: to avoid duplicates, skip if the orphan identifier + week + suggested parent combo already exists in `GET /api/v1/epics-police/decisions?week=<monday>`.
5. If any inferred decisions were found, POST them as a batch to `POST http://localhost:8100/api/v1/epics-police/decisions`.
6. Log to stderr: `"Inferred N decisions (A accepted, R redirected)"`.

### Step 1 — Initialize

1. Parse the user's input for a week reference. Defaults:
   - No explicit week → current week
   - `last week` → previous Monday–Friday
   - `week of YYYY-MM-DD` → the working week containing that date
2. Call `GET http://localhost:8100/api/v1/health`. If the response is not HTTP 200, run `jura api restart` and retry once. If it still fails, abort with `"Management API unreachable — run \`jura api restart\` manually and check the logs"`.
3. Call `GET http://localhost:8100/api/v1/weeks`. Find the entry matching the target week's Monday. Extract `week_label` (e.g. `06-to-10`), `month_dir` (e.g. `04-2026`), and `monday` (e.g. `2026-04-06`).
4. If no matching week entry, compute locally as fallback:
   ```
   monday = target_date - target_date.weekday()
   friday = monday + 4
   month_dir = monday.strftime('%m-%Y')
   week_label = f'{monday.day:02d}-to-{friday.day:02d}'
   ```

### Step 2 — Dispatch 3 parallel Agent subagents

Launch **3 parallel agents** using the Agent tool. All three Agent tool calls MUST be in a single message to ensure parallel execution.

> **Note:** `{monday}` in the prompts below is a placeholder — substitute with the actual ISO Monday date (e.g. `2026-04-06`) from Step 1. Do not pass the literal string `{monday}` to the API.

**Agent 1 — Linear:**

```
Agent tool call:
  description: "fetch linear from management api"
  prompt: "Fetch all Linear tickets for the week of {monday}. Run this command and return the FULL output:

    python3 -c \"
import json, urllib.request
items, offset = [], 0
while True:
    url = f'http://localhost:8100/api/v1/linear?week={monday}&limit=5000&offset={offset}'
    data = json.loads(urllib.request.urlopen(url).read())
    items.extend(data['items'])
    if offset + data['limit'] >= data['total']: break
    offset += data['limit']
print(json.dumps({'items': items, 'total': len(items)}))
\"

  Return the complete JSON output."
  subagent_type: "general-purpose"
```

**Agent 2 — Epics:**

```
Agent tool call:
  description: "fetch epics from management api"
  prompt: "Fetch all epics for the week of {monday}. Run:

    curl -s 'http://localhost:8100/api/v1/epics?week={monday}&limit=5000'

  Return the complete JSON output."
  subagent_type: "general-purpose"
```

**Agent 3 — People:**

```
Agent tool call:
  description: "fetch people from management api"
  prompt: "Fetch all people. Run:

    python3 -c \"
import json, urllib.request
items, offset = [], 0
while True:
    url = f'http://localhost:8100/api/v1/people?limit=5000&offset={offset}'
    data = json.loads(urllib.request.urlopen(url).read())
    items.extend(data['items'])
    if offset + data['limit'] >= data['total']: break
    offset += data['limit']
print(json.dumps({'items': items, 'total': len(items)}))
\"

  Return the complete JSON output."
  subagent_type: "general-purpose"
```

Wait for all 3 agents to complete. If any agent fails, log the failure, add the source name to `meta.degraded_sources`, and continue with the data you have. Store results as:

- `linear_data` — array of LinearTicket objects
- `epics_data` — array of Epic objects
- `people_data` — array of Person objects

### Step 2.5 — Fetch learnings

Fetch learned weights and patterns from the feedback loop:

```
GET http://localhost:8100/api/v1/epics-police/learnings
```

The response contains:

- `learned_weights` — Bayesian-updated signal weights (sum to 100). Use these **instead of** the hardcoded weight table in Step 6 if `sufficient_data` is true.
- `learned_thresholds` — calibrated thresholds. Use these instead of the tunables defaults if `sufficient_data` is true.
- `confidence_calibration` — precision per confidence band. Include in the Pass 2 LLM prompt so it knows which bands are trustworthy.
- `structural_patterns` — long-term patterns (e.g., "epic X has 80% rejection rate", "tickets with label Y mislead matching"). Feed relevant patterns into the Pass 2 prompt as anti-pattern context.
- `signal_effectiveness` — per-signal lift. Informational — already baked into `learned_weights`.

**If the endpoint returns defaults** (`sufficient_data: false`), use the tunables block below as-is. No degraded_sources entry needed — this is the expected cold-start path.

Store the learnings response as `active_learnings` for use in Steps 6-8.

### Step 3 — Build indexes

Build the in-memory data structures:

1. **`people_by_id`** — `{person.id: {display_name, email, squad, slack_user_id}}` from `people_data`
2. **Load ignore list**. Fetch `GET http://localhost:8100/api/v1/config/epics_police/ignored_assignees` and extract the `value` array. Build `ignored_person_ids = {entry.person_id for entry in value if entry.person_id}`. If the endpoint returns 404, `ignored_person_ids = set()` and continue. Record the count of ignored IDs in `meta.ignored_assignee_count` so it surfaces in the report header.
3. **Filter ticket list**. Before building `tickets_by_id`, drop every ticket whose `person_id` is in `ignored_person_ids`. Log the count dropped to stderr: `"Filtered N tickets from ignored assignees: <names>"`. This filter runs BEFORE classification so excluded tickets never enter Pass 1 scoring, Pass 2 LLM calls, parent-chain walks, declared-epic trees, or compliance stats. **Important for tree integrity**: if an ignored ticket is a *parent* of a non-ignored ticket, the child will be treated as an "out-of-cycle parent" orphan. This is intentional — the ignore list is rarely applied to people who lead epic subtrees; if it ever is, document the edge case in the output.
4. **`tickets_by_id`** — for every REMAINING ticket in `linear_data`, construct:
   ```jsonc
   {
     "identifier": ticket.identifier,
     "title": ticket.title,
     "parent_identifier": ticket.parent_identifier,
     "child_identifiers": ticket.child_identifiers or [],
     "labels": ticket.labels or [],
     "status": ticket.status,
     "status_type": ticket.status_type,
     "priority_label": ticket.priority_label,
     "points": ticket.points,
     "person_id": ticket.person_id,
     "assignee_name": people_by_id.get(ticket.person_id, {}).get("display_name", "unassigned"),
     "assignee_squad": people_by_id.get(ticket.person_id, {}).get("squad"),
     "description": ticket.description or "",
     "url": ticket.url,
     "is_bug": any(lbl.lower() in BUG_LABEL_SET for lbl in (ticket.labels or [])),
     "is_epic_prefixed": bool(re.match(r"^\s*\[EPIC\]", ticket.title or "")),
   }
   ```
5. **`roots`** — list of `identifier` for every ticket where `parent_identifier` is `None`
6. **`notion_epics`** — for every epic in `epics_data`, keep `{title, status, team, pm_lead, content, sub_pages, notion_page_id, properties}`. Flag `active = status in ACTIVE_NOTION_STATUSES`.

### Step 4 — Classify roots

For each root ticket (from `roots`):

| Condition | Classification |
|---|---|
| `is_epic_prefixed == true` | `declared_linear_epic` |
| has ≥ `IMPLICIT_EPIC_MIN_DIRECT_CHILDREN` direct children OR ≥ `IMPLICIT_EPIC_MIN_DESCENDANTS` descendants total | `implicit_epic_candidate` |
| otherwise | `standalone` (treated as orphan) |

Store `root_classification` on each root.

### Step 5 — Walk parent chains

For every ticket with a parent, walk `parent_identifier` up to the root, capped at `PARENT_WALK_MAX_DEPTH` (10) as a cycle guard. Record `root_id` and `root_classification` on each ticket.

Outcomes:

- `root_classification == declared_linear_epic` → **compliant** (ticket ladders up correctly)
- Anything else → **orphan** (the tree top is not an `[EPIC]`)

**Cycle detection:** if depth exceeds the cap, mark the ticket with `walk_error: "cycle"`, treat it as a root, and continue.

**Out-of-cycle parents:** if a ticket's `parent_identifier` points to an identifier not present in `tickets_by_id`, treat the ticket as a root and record a reason `"parent TEAM-XXXX not in current cycle"` for the unparented section.

### Step 6 — Pass 1: deterministic scoring

For each orphan, score it against every `declared_linear_epic`, every active Notion epic, and every other `implicit_epic_candidate`.

**Weight selection:** If `active_learnings.sufficient_data` is true, use `active_learnings.learned_weights`. Otherwise, use the default weight table below. Log which weights are active to stderr: `"Using learned weights (N decisions)"` or `"Using default weights (no decision history)"`.

Default weight table (also serves as Bayesian priors):

| Signal | Weight | Computation |
|---|---|---|
| Shared labels (excluding bug labels) | 35 | Jaccard over `labels` sets |
| Title token overlap | 25 | Jaccard over stop-word-stripped tokens in title |
| Description keyword overlap | 20 | Jaccard over top-20 content keywords |
| Assignee-squad match | 10 | +10 if orphan's squad == candidate's inferred squad |
| Notion `Team` field match (Notion candidates only) | 10 | +10 if `assignee_squad` maps to Notion `Team` |

Tiers:

- **≥ `PASS1_LOCK_THRESHOLD` (70)** → high-confidence, locked, no LLM pass
- **`PASS1_AMBIGUOUS_FLOOR` (40) to 69** → ambiguous, send to Pass 2
- **< 40** → unmatched, send to Pass 2 as a "new cluster candidate"

Record the top-3 candidate scores per orphan for debuggability in the analysis JSON. **Critical for the feedback loop**: for each orphan match, also record the per-signal breakdown as a `signals` object: `{"label_overlap": 28, "title_overlap": 15, "description_overlap": 10, "squad_match": 10, "notion_match": 0}` and a `match_source` field (`"pass1"` or `"pass2"`). The UI uses these fields to log structured decisions when the user accepts or rejects a suggestion.

### Step 7 — Pass 2: LLM semantic pass (narrow scope)

Only run this step if there are ambiguous or unmatched orphans.

Send one focused LLM call with:

- The ambiguous orphan list: title, description[:500], labels, assignee squad, Pass 1 top-3 candidates with scores
- The declared `[EPIC]` list: identifier, title, description[:300]
- The active Notion epic list: title, content[:500], team
- The implicit epic candidate list: identifier, title, description[:300]

LLM task (single structured response):

1. For each orphan, confirm one of the Pass 1 candidates OR propose a different match
2. For orphans that match nothing existing, cluster them into new proposed `[EPIC]`s and generate `[EPIC] <title>` names
3. For each new proposed epic, name the closest matching Notion epic if any
4. Return a JSON object keyed by orphan identifier:

```jsonc
{
  "TEAM-1741": {
    "match_type": "proposed_epic",
    "match_id_or_proposed_title": "[EPIC] Discounts & Promo Codes",
    "confidence_0_100": 88,
    "reason": "Title contains 'promo code', clusters with TEAM-1742 and TEAM-1755"
  },
  "TEAM-1890": {
    "match_type": "none",
    "match_id_or_proposed_title": null,
    "confidence_0_100": 0,
    "reason": "No signal overlap with any existing or proposed epic"
  }
}
```

**If the LLM response is not valid JSON matching this schema:** retry once with an explicit "return strictly this JSON schema" re-prompt. If it still fails, fall back to Pass 1 scores only and add `llm_clustering_failed` to `meta.degraded_sources`.

### Step 8 — Rescale confidences

Apply the rescale rules:

- Pass 1 scores ≥ `PASS1_LOCK_THRESHOLD` → keep as-is
- Pass 2 scores × `PASS2_CONFIDENCE_SCALE` (0.85), capped at `PASS2_CONFIDENCE_CAP` (95)

Final bands (used by the UI, not section assignment):

| Band | Threshold | Badge |
|---|---|---|
| High | ≥ `RENDER_HIGH_BAND` (80) | ✅ |
| Medium | ≥ `RENDER_MID_BAND` (60) | ⚠ |
| Low | < 60 | ❓ |

### Step 9 — Assemble the analysis JSON

Build the full analysis object. Populate every top-level field even if empty (use `[]`, `{}`, or `0`):

- `meta` — from Step 1, plus `generated_at` (current ISO timestamp) and `degraded_sources` (list populated as failures occurred)
- `compliance_snapshot` — computed counts (see below)
- `tickets_by_id` — full map from Step 3
- `declared_epics` — for each declared epic root, build:
  - `identifier`, `title`, `pm_lead` (from assignee), `squad`, `status`, `points_done`, `points_total`, `direct_children`
  - `tree` — flat map `{child_id: {"children": [...]}}` for every descendant
  - `matched_orphans` — orphans matched to this epic with confidence ≥ `FEATURE_MATCH_THRESHOLD`
- `proposed_epics` — from the Pass 2 new-cluster output
- `implicit_candidates` — roots with `root_classification == implicit_epic_candidate`; build `tree` from their descendants, include `suggested_title = "[EPIC] " + current_title.lstrip("[PARENT] ").lstrip()` or the LLM's suggestion if one was returned
- `unparented` — feature orphans with no match (confidence < `FEATURE_MATCH_THRESHOLD`) and not a bug
- `bugs` — **only bugs whose structural root is NOT a declared `[EPIC]`** (i.e. genuine bug orphans). Bugs that already ladder up to a declared epic via `parent_identifier` are structurally compliant and must NOT appear in any of the bugs section buckets — they're already covered by the declared epic's tree. The genuine bug orphans are then bucketed into `matched_to_epic` / `suggested_epic_low_confidence` / `standalone` using `BUG_MATCHED_THRESHOLD` (70) and `BUG_SUGGESTED_FLOOR` (40). **Critical**: this filter goes BEFORE Pass 2 — don't send already-compliant bugs to the LLM for classification.
- `dormant_notion_epics` — active-status Notion epics (`status in ACTIVE_NOTION_STATUSES`) with zero matches in this week's Linear data
- `pass1_signals` — the weight table and thresholds used, for debuggability

**Compliance snapshot computation:**

```
feature_tickets = [t for t in tickets_by_id.values() if not t.is_bug]
bugs = [t for t in tickets_by_id.values() if t.is_bug]

feature_total = len(feature_tickets)
feature_compliant = count(feature root is declared_linear_epic) + count(orphans matched to declared epic with conf >= FEATURE_MATCH_THRESHOLD)
feature_orphaned = feature_total - feature_compliant
feature_compliance_pct = round(feature_compliant / feature_total * 100) if feature_total else 0

bug_total = len(bugs)
bug_structurally_compliant = count(b for b in bugs if walk_to_root(b).classification == "declared_linear_epic")
bug_routed_compliant = count(bugs in bugs.matched_to_epic)
bug_compliant = bug_structurally_compliant + bug_routed_compliant
bug_unparented = count(bugs in bugs.standalone)
bug_compliance_pct = round(bug_compliant / bug_total * 100) if bug_total else 0

declared_epic_count = len(declared_epics)
implicit_candidate_count = len(implicit_candidates)
proposed_epic_count = len(proposed_epics)
dormant_notion_count = len(dormant_notion_epics)
```

### Step 10 — Push analysis to API

Push the analysis JSON to the Management API:

```python
import json, urllib.request

analysis_json = json.dumps(analysis, ensure_ascii=False).encode('utf-8')
req = urllib.request.Request(
    'http://localhost:8100/api/v1/epics-police/analysis',
    data=analysis_json,
    headers={'Content-Type': 'application/json'},
    method='POST',
)
try:
    urllib.request.urlopen(req)
except Exception as e:
    # Log warning but don't abort — the analysis is computed, just couldn't store it
    print(f"Warning: failed to push analysis to API: {e}", file=sys.stderr)
```

If the push fails, abort with a clear error — the UI cannot display results without the analysis being stored.

### Step 11 — Distill and calibrate

After pushing the analysis, trigger re-distillation and self-calibrate:

1. **Trigger distillation**: `POST http://localhost:8100/api/v1/epics-police/distill`. This recomputes learned weights from all stored decisions (including any inferred in Step 0). The response includes `learned_weights`, `learned_thresholds`, and `weights_changed`.

2. **Self-calibrate** (if `weights_changed` is true and `total_decisions >= 1`): Compare the distilled `learned_weights` to the current tunables block in this SKILL.md file. If any weight differs by more than 5% (absolute), update the tunables block:
   - Read the current SKILL.md file at `.claude/skills/epics-police/SKILL.md`
   - Find the tunables block (delimited by ` ``` ` after `## Tunables`)
   - Update the weight and threshold values to match the learned values
   - Write the file back

   Log to stderr: `"Calibrated: weights updated (label_overlap 35→32, squad_match 10→18, ...)"` listing each changed value.

   If weights have NOT diverged by >5%, skip the file write and log: `"Calibration check: weights stable, no update needed"`.

3. **Report learnings in summary** (Step 13): Include a `Learning:` line showing decision count and whether weights were updated.

### Step 12 — Open the interactive UI

```bash
open http://localhost:8100/epics-police
```

### Step 13 — Report summary to stdout

Print a compact summary:

```
Epics Police — Week {week_label} ({month_name} {yyyy})

Feature compliance: {feature_compliance_pct}% ({feature_compliant}/{feature_total})
Bug compliance:     {bug_compliance_pct}% ({bug_compliant}/{bug_total})

Declared [EPIC]s:   {declared_epic_count}
Implicit candidates:{implicit_candidate_count}  (promote to [EPIC])
Proposed missing:   {proposed_epic_count}
Dormant Notion:     {dormant_notion_count}
Learning:           {total_decisions} decisions | weights {'updated' or 'stable'}

Top actions:
  1. {top action 1 — an implicit candidate to promote, or a proposed epic to create}
  2. {top action 2}
  3. {top action 3}

UI: http://localhost:8100/epics-police (opened in browser)
```

The top-3 actions are your judgment call based on which rows have the highest confidence and the biggest structural impact (an implicit candidate with 11 descendants is higher priority than a proposed epic with 3).

### Step 14 — Stop

Do not commit, do not push, do not run any git command (except the SKILL.md self-calibration write in Step 11, which is a local file edit, not a git operation). The user will review the report and act on suggestions through the interactive UI.

---

## Tunables

Edit this block to tune the skill's matching behavior. **These values serve as Bayesian priors and are automatically updated by the self-calibration loop in Step 11** when learned weights diverge by >5%. The git diff of this block shows how the algorithm evolves over time.

```
# Implicit epic candidate detection
IMPLICIT_EPIC_MIN_DIRECT_CHILDREN = 2
IMPLICIT_EPIC_MIN_DESCENDANTS = 4

# Pass 1 deterministic scoring — signal weights (Bayesian priors, auto-calibrated)
WEIGHT_LABEL_OVERLAP = 31.0      # was 29.7 — stable (within 5% tolerance)
WEIGHT_TITLE_OVERLAP = 48.1      # was 50.6 — stable (within 5% tolerance)
WEIGHT_DESCRIPTION_OVERLAP = 15.6 # was 14.4 — calibrated 2026-04-20 (31 decisions, +8.3%)
WEIGHT_SQUAD_MATCH = 2.7         # was 2.6 — stable (within 5% tolerance)
WEIGHT_NOTION_MATCH = 2.7        # was 2.6 — stable (within 5% tolerance)

# Pass 1 deterministic scoring — thresholds
PASS1_LOCK_THRESHOLD = 70       # ≥70 locks a match, skips Pass 2
PASS1_AMBIGUOUS_FLOOR = 40       # 40-69 → Pass 2; <40 → Pass 2 as new-cluster candidate

# Pass 2 LLM confidence rescaling
PASS2_CONFIDENCE_SCALE = 0.85
PASS2_CONFIDENCE_CAP = 95

# Render bands (visual badges on matched tickets)
RENDER_HIGH_BAND = 80            # ✅
RENDER_MID_BAND = 60             # ⚠
                                 # <60 → ❓

# Section assignment thresholds
FEATURE_MATCH_THRESHOLD = 60     # features ≥60 → under epic; <60 → unparented
BUG_MATCHED_THRESHOLD = 70       # bugs ≥70 → bugs.matched_to_epic
BUG_SUGGESTED_FLOOR = 40         # bugs 40-69 → bugs.suggested_epic_low_confidence
                                 # bugs <40 → bugs.standalone

# Misc
BUG_LABEL_SET = {"bug", "prod", "hotfix", "incident"}
PARENT_WALK_MAX_DEPTH = 10
ACTIVE_NOTION_STATUSES = {"Prioritised", "In exploration", "Design", "In development", "In UAT"}
```

## Invocation

- Slash command: `/epics-police`
- Natural language: `police the epics`, `check orphans`, `audit epics`, `who has no parent`, `are we clean`, `epics police this week`, `epics police last week`

## Error Handling

| Situation | Behavior |
|---|---|
| `/api/v1/health` is down | Run `jura api restart`, retry once. If still down, abort with `"Management API unreachable"` |
| `/api/v1/linear` returns 0 tickets for the week | Push an analysis JSON with empty sections. The UI will show "No data for this week". |
| `/api/v1/epics` returns 0 or fails | Continue without Notion signals. Add `notion_unavailable` to `meta.degraded_sources`. |
| `/api/v1/people` fails | Continue, skip squad-based scoring signals. Add `people_unavailable` to degraded sources. |
| Parent walk hits a cycle (depth > `PARENT_WALK_MAX_DEPTH`) | Mark ticket with `walk_error: "cycle"`, treat as root, continue. |
| Ticket's `parent_identifier` points to a ticket not in the current week's data | Treat as root (out-of-cycle parent). Record reason `"parent TEAM-XXXX not in current cycle"` in unparented. |
| LLM Pass 2 returns malformed JSON | Retry once with strict-schema re-prompt. If still bad, fall back to Pass 1 scores only and add `llm_clustering_failed` to degraded sources. |
| POST analysis fails | Abort — the UI can't display results without the stored analysis. |
| Natural language trigger is ambiguous (e.g. bare "audit") | Ask a 1-line clarification: `"audit epics for which week?"` |
