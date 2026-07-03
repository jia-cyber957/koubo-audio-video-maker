# Assembly Contract

装配阶段输入：

- 音频字幕 bundle 解压目录：`*_cut.wav`、`*_cut_subtitles.srt`、`*_cut_subtitles_words.json`、`*_cut_timeline.json`
- 素材项目目录：`素材分段计划.json`、`素材选择结果.json`、章节素材目录

准备装配：

```powershell
node "$SKILL_DIR\scripts\prepare.js" --bundle "$BUNDLE_DIR" --materials "<素材项目目录>" --out "<装配目录>"
```

若 `<装配目录>/对齐任务.json` 的 `unresolved` 非空：

- 只处理 unresolved 列出的素材点。
- 在 `<装配目录>/素材时间对应.json` 填写对应条目的 `start`、`end`、`match_method: "ai"`、`reason`。
- `start`/`end` 必须来自连续字幕 cue 的时间范围。
- 不改动已精确匹配条目。
- 填完后重新运行 `prepare.js`。

校验和审片：

```powershell
node "$SKILL_DIR\scripts\validate.js" "<装配目录>"
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\serve_review.ps1" -ProjectDir "<装配目录>"
```

约束：

- 未解决时间对应时，不启动审核页，不导出。
- 一个短分镜默认对应一个视频位置；其他下载视频是替换候选，不自动串联。
- 素材原声关闭。
- MP4 为 1920x1080、30fps、H.264/AAC；空分镜使用黑画面。
- MP4 不烧录字幕，同目录 SRT 单独保留。
- 找不到 ffmpeg 时仍可审核和替换素材，但不能合成 MP4；需要安装 ffmpeg 后再导出成片。
