# Snapdraft comparison and dictation latency

Checked 2026-10-24. This report supports the ongoing dictation performance fix.

Snapdraft documents local `whisper.cpp` using `base.en` and local `llama.cpp`
using Qwen3-4B. Its public repository contains documentation, website assets and
release artifacts, rather than application source. Its scheduling, streaming,
warmup and exact GPU configuration cannot be established from that repository.

Sources: [privacy policy](https://github.com/ranyitz/snapdraft/blob/main/PRIVACY.md),
[repository](https://github.com/ranyitz/snapdraft),
[distribution description](https://github.com/ranyitz/snapdraft/blob/main/index.md).

Harness Discussion already uses Qwen3-4B for local cleanup, but its Windows AMD64
runtime is explicitly the CPU build of llama.cpp b11738. That is an observed
configuration difference, rather than proof of how Snapdraft executes its model.

Measured on this PC with synthetic English input, using the same cleanup model
and prompts: CPU cleanup took 2.6 to 5.6 seconds warm. The official b11738 Vulkan
runtime cleaned two different drafts in 0.30 and 0.79 seconds. A fresh server took
3.86 seconds to load after the initial shader cache had been created. First-ever
initialization was slower: 9.67 seconds to load and 11.59 seconds for its first
request. Warm timings do not describe first-ever use or native insertion latency.

The [official b11738 release](https://github.com/ggml-org/llama.cpp/releases/tag/b11738)
provides a 32,358,940-byte Windows x64 Vulkan archive with SHA-256
`ef623c883e0b67e84b92ca5fde04b65b74ef3691898b9510ff335c8d601076bd`.
It includes Vulkan and CPU backends. The benchmark downloaded and verified that
artifact in a temporary directory and reused the existing model without changing
the installed application's files or restarting it.

Speech recognition measurements are separate: a 7.8-second sample took about
2.6 seconds on CPU and 0.2 seconds warm on the RTX 4080 Super. A repeated
57-second recording took 43.09 seconds on CPU and 10.99 seconds on GPU. Both
used multilingual Whisper Small through faster-whisper. Choosing English-only
`base.en` would require a language/accuracy tradeoff that these tests do not settle.

Inference: the measured GPU runtime difference is sufficient to explain much of
our cleanup delay. It supports accelerating the existing model before replacing
it with a smaller one. Native microphone-to-insertion behavior, macOS performance,
and real Hebrew/mixed speech quality still require separate verification.
