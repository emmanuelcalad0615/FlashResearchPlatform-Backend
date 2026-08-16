# Notes — understand-anything

This repo has a generated knowledge graph under `.understand-anything/`, produced by
the `understand-anything` Claude Code plugin (`/understand-anything:understand`,
aliased here as `/understand`). This file is a quick operational reference —
not project documentation. See `architecture.md`, `database.md`, and
`communication.md` for the actual backend docs.

## What's in `.understand-anything/`

```
.understand-anything/
  knowledge-graph.json    # the graph: nodes, edges, layers, tour (dashboard reads this)
  meta.json               # lastAnalyzedAt, gitCommitHash, analyzedFiles — drives incremental updates
  fingerprints.json       # per-file structural fingerprints baseline, used to detect real vs. cosmetic changes
  config.json             # { outputLanguage, autoUpdate } — persisted skill preferences
  .understandignore       # gitignore-style excludes for analysis (separate from .gitignore)
  intermediate/
    scan-result.json      # kept after cleanup so incremental runs can skip the SCAN phase
  .trash-<timestamp>/     # previous run's scratch files, purged automatically after 7 days
```

`knowledge-graph.json` was generated at commit `001dfd9` (76 nodes, 107 edges, 7
layers, 14 tour steps). Note `.claude` is gitignored in this repo, so
`.understand-anything/` (also gitignored via the same rule tree, check `.gitignore`)
is **local-only** — it won't sync across machines or land in PRs. Re-run the
skill after cloning fresh or on a new machine.

## Updating the graph

Just re-run the skill:

```
/understand-anything:understand
```

- **If nothing changed since the last run** (same commit hash in `meta.json`), it
  asks whether to do a full rebuild, run the LLM reviewer only, or do nothing.
- **If files changed** (`git diff <lastCommitHash>..HEAD`), it runs an
  **incremental update**: only changed files get re-batched and re-analyzed by
  the file-analyzer subagents; unchanged nodes/edges are carried forward and
  stitched back in. Much cheaper than a full run for small diffs.
- **Force a full rebuild** (ignores the existing graph entirely):
  ```
  /understand-anything:understand --full
  ```
- **Run the deeper LLM graph-reviewer** instead of the fast inline validator
  (useful after a big refactor, or if you don't trust the last run):
  ```
  /understand-anything:understand --review
  ```
- **Auto-update on commit** — writes `autoUpdate: true` to
  `.understand-anything/config.json` (the flag itself doesn't wire up a git hook;
  it's a preference read by whatever automation triggers the skill):
  ```
  /understand-anything:understand --auto-update
  ```
  Disable with `--no-auto-update`.

Before any run, it's worth glancing at `.understand-anything/.understandignore` —
currently everything is commented out (all 41 project files get analyzed,
including `migrations/`, `scripts/`, `tests/`). Uncomment patterns there if the
graph should exclude something going forward (e.g. generated code, vendored
files) — it's independent of `.gitignore`.

## Relaunching the dashboard

The dashboard is a separate Vite dev server that reads `knowledge-graph.json`
live — it doesn't need a new `/understand` run, just needs to be restarted after
a reboot or if you closed the terminal.

**Easiest way:**
```
/understand-anything:understand-dashboard
```

**Manually**, if you need to run it detached from a skill invocation:
```bash
PLUGIN_ROOT="/Users/andrew/.claude/plugins/cache/understand-anything/understand-anything/2.8.1"
DASHBOARD_DIR="$PLUGIN_ROOT/packages/dashboard"
PROJECT_DIR="/Users/andrew/Documents/Projects/flash/FlashResearchPlatform-Backend"

cd "$DASHBOARD_DIR" && GRAPH_DIR="$PROJECT_DIR" npx vite --host 127.0.0.1
```

The server prints a line like:
```
🔑  Dashboard URL: http://127.0.0.1:5173/?token=<TOKEN>
```
**The `?token=` query param is required** — without it the dashboard shows an
"Access Token Required" gate instead of the graph. Default port is 5173; if it's
taken, Vite picks the next free one (check the server's stdout for the actual
port). The server keeps running until you kill it (Ctrl+C, or kill the
background job) — it's not tied to the Claude Code session.

If the plugin has been updated/reinstalled and the version-pinned path above no
longer exists, resolve `PLUGIN_ROOT` by checking, in order:
`~/.understand-anything-plugin` → the two-levels-up parent of the resolved
`~/.agents/skills/understand-dashboard` symlink → the Codex/OpenCode/pi
clone-based install paths. See the `understand-dashboard` skill source for the
exact resolution script if needed.

## Gotchas learned from the last run

- **Worktrees**: if `PROJECT_ROOT` is ever a git worktree (not the main
  checkout), the skill auto-redirects output to the main repo root, because
  worktree-local `.understand-anything/` gets destroyed when the worktree is
  cleaned up. Not applicable to this repo today (no worktrees in use), but keep
  it in mind if that changes.
- **Language**: `config.json` has `outputLanguage: "en"` — all graph text
  (summaries, tags, tour prose) is in English even though the repo's own
  comments/README are in Spanish. Override per-run with `--language <lang>` if
  that should change; it persists for future runs.
- **Redis / worker are provisioned but idle**: the graph faithfully reflects
  that `apps/worker` and Redis have no real code wiring yet (see
  `architecture.md` §4 notes) — don't be surprised the tour and layers treat them
  as skeleton/unused rather than inferring a pipeline that isn't implemented.
