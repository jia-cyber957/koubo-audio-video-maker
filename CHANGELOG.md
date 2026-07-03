# Changelog

## 0.1.0

- Added single-audio entry workflow.
- Added Volcengine transcription and word-level timeline bridge.
- Added semantic storyboard requirements under 10 seconds.
- Added Pexels/Pixabay multi-round material search.
- Added LLM/Codex text scoring workflow.
- Removed visual review and contact sheet path.
- Added resumable download behavior for existing non-empty `video_XX.mp4` files.
- Added batched downloads: `download` processes at most 10 material points per run by default, with smaller final batches allowed.
- Optimized review page performance: lightweight timeline cards, no hover autoplay, fewer video elements, media caching, HEAD and Range support.
- Added MP4 assembly handoff.
