# AgentTube Architecture Roadmap

## Direction

Evolve the existing AgentTube workflows in place. Keep Clip Studio, Viral Clone, Video Generator, Clip Editor, Automation Agent, and Social Publishing usable as distinct workflows while sharing their project, media, job, review, and publishing foundations.

The deployment target is local-first hybrid. Local files, credentials, and provider calls stay on the user's machine by default. Cloud control or processing is optional and must be explicitly configured; syncing media or secrets is never implicit.

## Current State

The repository already has a shared dashboard and several working workflows, but they still use module-specific data and job paths. Viral Clone supports linked or uploaded references, hook variants, and reference-guided rendering. Phase 0 foundations are in progress: SQLite foreign-key enforcement and concurrency settings, per-Short scheduling identity, and a startup guard that rejects non-loopback binds without `API_KEY` are present.

The current Node test suite passes the database, Shorts scheduling, and host-security checks. One separate Gemini Media Provider Selection test still fails because its Edge TTS test expects text on stdin. The broader roadmap is not yet implemented.

## Phases

### Phase 0: Reliability and security foundations

- [x] Enforce SQLite foreign keys and configure a busy timeout and WAL.
- [x] Keep each Short's schedule identity distinct while retaining its parent production ID.
- [x] Bind to loopback by default and require `API_KEY` before allowing a non-loopback bind.
- [ ] Resolve the Edge TTS stdin test failure and establish a green baseline suite.
- [ ] Document backup, restore, and database migration expectations.

### Phase 1: Shared workspace primitives

- [ ] Add projects/workspaces that can contain outputs from every existing workflow.
- [ ] Add a shared media catalog with stable asset IDs, provenance, metadata, and local paths; keep media bytes local by default.
- [ ] Add reusable Brand Kits for visual identity, voice, and publishing defaults.
- [ ] Migrate existing records incrementally, preserving legacy IDs and existing dashboard flows.

### Phase 2: Common job and review lifecycle

- [ ] Define a durable job record and status model used by clipping, generation, editing, and repurposing.
- [ ] Add consistent progress, cancellation, bounded retries, and failure details.
- [ ] Make review/approval state reusable across workflows without changing each module's specialized controls.
- [ ] Keep publishing gated on explicit review and required asset checks.

### Phase 3: Shared creative workflows

- [ ] Connect reference analysis, scripts, hooks, renders, edits, and publishing through project and asset IDs.
- [ ] Add 2D, 3D, and UGC-style generation as selectable engines behind shared job and review contracts.
- [ ] Track reference provenance, permissions, and derived-asset lineage.
- [ ] Preserve the existing Clip Studio and Viral Clone local workflows during migration.

### Phase 4: Optional hybrid services

- [ ] Define explicit local/cloud execution policies per job and media asset.
- [ ] Add authenticated sync and job dispatch only after project, media, and job contracts are stable.
- [ ] Keep credentials in a dedicated secret store; never sync local provider credentials as project data.
- [ ] Add deployment health, audit, retention, and recovery behavior before enabling remote processing by default.

### Phase 5: Publishing and analytics expansion

- [ ] Add additional social-platform adapters behind a shared publishing contract.
- [ ] Normalize schedules, upload outcomes, retries, and reconciliation across providers.
- [ ] Connect analytics back to projects and published assets while preserving channel-level reporting.

## Change Rules

- Prefer additive migrations and adapters over replacing working modules.
- Keep local use fully functional when cloud services are not configured.
- Treat API keys, OAuth tokens, and media sync as security-sensitive; fail closed for remote access.
- Add focused tests for migrations, cross-workflow identity, job state transitions, and publish idempotency.
- Mark a phase complete only when its acceptance checks pass, not when its scaffolding exists.
