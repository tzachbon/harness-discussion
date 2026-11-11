# Plan: Meeting notes for Harness Discussion

Read the parts in order. Each work packet in part 5 names the sections of parts 3 and 4 it needs, so an implementer
with no conversation history can start from its packet.

1. [1-context.md](1-context.md): Objective, Definition of Done, evidence (repo facts and spike results)
2. [2-requirements.md](2-requirements.md): Requirements, fixed decisions, scope, approach, privacy rules
3. [3-ux.md](3-ux.md): Flows, exact copy, ASCII mockups per platform, accessibility
4. [4-design.md](4-design.md): Modules and APIs, on-disk layout, threads, state machine, detection, calendar, notes prompts, dependencies, packaging
5. [5-work-plan.md](5-work-plan.md): Tasks T1 to T22 as Codex work packets, parallel groups
6. [6-verification.md](6-verification.md): Acceptance table, simulated-call procedure, the owner's real-call checklist, risks, open items

Supporting files: [sim-call.html](sim-call.html), the page agents use for the simulated call (part 6), and
`spikes/sck_audio.py` and `spikes/mic_procs.py`, the Mac spike scripts the Mac capture packets copy from.

Status: design complete, nothing implemented. Branch: one feature branch, one PR for both platforms. Mac first, then Windows.
