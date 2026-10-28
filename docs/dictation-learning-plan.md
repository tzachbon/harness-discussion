# Dictation correction learning on Windows and macOS

## Outcome and completion

The requested outcome is automatic vocabulary learning from edits to text just dictated into another app, on both supported platforms. Correct a misheard name, then have its preferred spelling available to later dictation. The user explicitly invoked `execute-task-end-to-end`: create an issue, implement, verify, publish a PR, and reach provider-verified MERGE READY. Merging is not authorized.

Completion requires current-head Windows/macOS tests and builds, independent review with no actionable findings, and an open, non-draft, mergeable PR with required checks/reviews satisfied and no unresolved review conversations. Runtime evidence must distinguish the real insertion/observation path from mocked provider/model checks. Unavailable native Mac access is a validation blocker to report, never a reason to call mocked tests native verification.

## Context and evidence

Starting checkout: `research/dictation-learning`, `c4fdc68b321d56b8795e5cabdd993fa0c1b640ff`; common Git directory `/root/harness-discussion/.git`. The only existing dirty file is the preceding research report, which belongs to this task.

The report `research/2026-10-28-dictation-correction-learning.md` inspected OpenWhispr at `fc786f41c91de8177ce7d9333d2834d973cc3c19` and Wispr's official documentation. It confirms bounded accessibility observation, local word extraction, persistent canonical vocabulary and inference hints. Reuse the mechanism in the Python stack; do not vendor Electron helpers or train a model. Research depth is sufficient to choose the mechanism; app/editor compatibility remains a runtime question.

Existing `app_dictation.Dictation._dictated` performs guarded insertion and clipboard fallback. Windows and Mac `edit.observe()` return bounded non-secure `edit_logic.Field` snapshots; deployed `app_live.App` already has a 500 ms background poll even when Check as I type is off. Windows has an exact insertion receipt; Mac paste success alone does not prove exact insertion. `stt.transcribe` already accepts a vocabulary prompt; faster-whisper accepts `initial_prompt`; Cursor's inspected dictation protocol has no verified vocabulary channel. Dictation tidy uses the shared Writer context and can carry vocabulary on every writing source.

The user confirmed learning **on by default**, with an off switch, editable/removable vocabulary and Undo for learned batches.

## Requirements

- R1: After a confirmed direct dictation insertion, observe only that same non-secure, writable field for at most 30 seconds; process settled edits after 1.5 seconds. Clipboard-only and unconfirmed insertions never arm learning.
- R2: Extract conservative canonical vocabulary locally, including Unicode names and short word-joining corrections. Reject ordinary common-word edits, punctuation-only edits, additions/deletions, unrelated rewrites, paths, addresses, numbers, IDs and code-like input. Learning is heuristic; no claim of perfect human-edit provenance.
- R3: Persist at most 200 validated vocabulary entries using existing atomic settings writes. Display learned batches with Undo, and provide an off switch and edit/remove controls on both Dictation settings pages. Persistence failure must leave dictation intact and report failure without logging text.
- R4: Include a bounded vocabulary hint in Local and Codex transcription and in dictation tidy. Cursor raw transcription has no verified hint API; document that its vocabulary applies through tidy. Do not change the STT HTTP contract or send words during unrelated reading, summary or rewrite operations.
- R5: Cancel observation on changed target, unreadable/secure/read-only field, new dictation, manual rewrite, shutdown, timeout or settings disabling learning. Do not read other fields to determine that the target changed. Never use clipboard, selection writes or synthesized input for monitoring. Do not log words, documents or transcripts.
- R6: Preserve existing dictation cancellation, delivery, history, clipboard ownership, Unicode/UTF-16 handling and source order. No new dependencies, background services or permission changes.

## Technical / Coding

### Design and APIs

```text
successful guarded paste
  bounded before/after snapshot proves exact inserted text and caret
  learning.Watch holds original region plus immutable surrounding anchors
  existing Live poll reads edit.observe(context), allowing text/caret edits
  settled local word diff emits a generation-bound event
  main thread saves vocabulary and shows Undo
  next dictation snapshots vocabulary for STT hints and tidy context
```

Add optional `ctx=None` to both native `observe` functions. A bound observation checks process/window/native element before any text read, and rechecks focus/security after it. Windows `_inspect` can accept the same bound context; keep UIA on its existing worker. Mac uses `CFEqual` and avoids enabling accessibility features on another app while monitoring. Existing no-argument callers retain behavior.

Proposed `learning.py` contains pure correction extraction, bounded vocabulary normalization/hint formatting, and a small Watch state object. Watch uses UTF-16 helpers already in `live`, exact prefix/suffix anchors from the pre-paste field, and confirms the full expected post-paste document/caret before accepting edits. Outside-region mutation or invalid caret ends the watch. It never resumes after losing the field. A single 30-second watch uses the existing poll thread; no worker per paste. It emits at most one batch per settled field value. Main-thread events carry the watch identity, so stale events cannot save after cancel/disable/Undo.

Extraction uses stdlib `difflib.SequenceMatcher` on bounded word lists, then character similarity for replacement runs. Permit short replacement runs of at most three words per side, require similar alphabetic spellings, skip existing/common words and case/punctuation-only changes, and cap transcript/region length at 4,000 UTF-16 units and 300 words. Keep Unicode letters, apostrophes and hyphens, but reject numbers, underscores, URLs, paths and other symbols. Explicitly mark that conservative similarity misses distant mishearings; upgrade only with evidence. Normalization validates all persisted settings and caps each entry at 60 characters. Hints use most-recent words within a 1,200-character budget.

Arm only after before/after snapshots prove the exact insertion. Delayed confirmation may retry on the existing poll within a short bounded settling window; no guessed substring search. If the user edits before insertion can be proven, skip learning for that paste. Temporarily suspend automatic live checks while this watch is active so the app's own generated suggestions do not compete with correction capture; manual rewrite cancels learning. Polling cannot distinguish external autocorrection from a manual correction: apply the same conservative extraction and give Undo.

Extend `dictation.transcribe` and known Local/Codex adapters with optional prompt hints while preserving empty-hint calls and cancellation. Append sanitized vocabulary to the existing dictation-only Writer context; do not change general Writer behavior. Use existing source routing, without a classifier request or extra cloud call.

Keep native settings controls in existing styles. Store `dictation_learn` and `dictation_words` alongside existing dictation settings, not a new database. Controls must let users remove a mistaken word and clear the vocabulary; editing uses the same validation. Every manual vocabulary change synchronously cancels the active watch before mutating words, so queued batches cannot repopulate a cleared or edited list. Undo removes only this batch's still-present additions and cancels its watch so they are not immediately relearned. Longer cohesive existing modules stay together; splitting them merely to meet a line count would obscure lifecycle ownership.

### Workstreams and verification

T1 — shared learning and lifecycle (root). Add `learning.py` and focused tests; modify `app_dictation.py`, `app_live.py`, `app_writer.py` only where the lifecycle needs integration. Verify exact insertion, Unicode/surrogate-safe offsets, before/after anchors, multiple settled edits, timeout, focus loss, unconfirmed insertion, cancellation/disable/Undo, persistence failure and no learning from clipboard fallback. Queue a learning batch, manually clear/edit words, then dispatch the stale batch and prove it cannot restore them. New tests must fail on absent behavior before the minimum implementation.

T2 — bound native snapshots (isolated worker). Modify only `win/edit.py`, `mac/edit.py` and their focused tests. Preserve no-argument callers. Add tripwires proving changed field/password status blocks text reads, and tests for focus/security transitions during reads and malformed AX ranges. Integrate after reviewing the isolated diff.

T3 — vocabulary settings and inference (isolated worker, after shared contract fixed). Modify `settings.py`, `dictation.py`, `local_stt.py`, and focused tests for validation and adapter forwarding. Root integrates Dictation UI and tidy context. Verify prompt budgets, empty hints, disabled cloud sources, Local CPU retry, Codex cancellation, and legacy call compatibility. Cursor protocol stays unchanged.

T4 — native settings, documentation and complete proof (root). Extend both existing Dictation pages with the same on/off and vocabulary-edit behavior. Update README, STT.md and PRIVACY.md with observation duration, local persistence/removal, remote hint behavior and Cursor-raw limitation. Run targeted shared checks with isolated APPDATA/LOCALAPPDATA. Run repository-required `uv run python -m unittest discover -s tests` on both hosted OSes and existing installer builds. Check resource load before heavy work; serialize it.

Use the available Windows MCP to drive a disposable editor/app fixture through confirmed insertion, manual correction, learned vocabulary, next inference hint and Undo/off controls. Preserve the user's clipboard and close only task-owned processes. Attempt supported Mac native access; if unavailable, retain CI/native-boundary evidence separately and leave the missing runtime proof explicit. Do not claim that a mocked microphone/provider proves speech recognition accuracy.

T5 — independent review and publication. Review the complete diff plus connected native guards, fix actionable findings, repeat affected checks, then create the linked PR using create-pr. Discover PR owners before registering this thread's watch; the root alone owns maintenance. Resolve CI/review findings to the exact-head MERGE READY conditions, then unwatch and hand back. Do not merge.

## Acceptance mapping

R1/R5/R6 map to T1/T2 and insertion/monitor safety tests plus native runtime evidence. R2/R3 map to T1/T3/T4 and extraction, validation, UI persistence/removal and Undo checks. R4 maps to T3/T4 and actual adapter request/prompt assertions. Final repository checks, independent review and provider readiness map to T5. Unavailable native proof remains a blocker requiring an explicit disposition, rather than silently lowering acceptance.

## Impact, risks and recovery

Benefit: names and jargon corrected once can influence later dictation. Cost: bounded accessibility reads after paste and a small private word list. Blast radius covers native reads, dictation lifecycle, settings persistence and STT/tidy context; it excludes reading, summaries, servers and unrelated rewrites. Code and settings are reversible; any remote request already sent cannot be recalled. No production deployment occurs before an explicitly authorized merge.

The main risks are learning from the wrong field, unsafe text reads, false vocabulary, stale worker events and hints changing inference. Binding before reads, exact region proof, bounded extraction, main-thread generation checks, editable words/Undo and prompt limits address them. Any native target uncertainty skips learning while preserving delivered dictation. An atomic settings failure keeps the previous saved list; report without content. Roll back the feature commit or turn learning off; users can remove stored words independently.

## Skill handoff and review ledger

Installed workflow skills: execute-task-end-to-end, create-plan, keep-it-simple, show-me, agent-swarm, ponytail, create-pr, pr-babysit and recap. Windows runtime uses windows-desktop-access. Apply specialist skills at their phases; no installation requested.

External discovery COMPLETE: `npx --yes skills find 'python desktop accessibility'` returned community accessibility/Python candidates (largest 132 installs); `npx --yes skills find 'python testing speech transcription'` returned an unrelated avatar-video candidate. No external candidate qualified for the actual native APIs or inference contracts, and none was installed. Existing installed skills and repository tests cover implementation. Prior art is the already-read official Wispr documentation and pinned OpenWhispr source report. This task adapts those mechanisms while keeping the native Python platform guards.

Planning review cap: three attempts. Attempt 1 found one P2: manual vocabulary edits must invalidate queued learning. The contract and deterministic test above resolve it; root rechecked event guards against all settings mutation paths. Remaining plan scope was accepted by independent review. Native boundary exploration is completed and incorporated above.
