# Storyboard And Search Planning

生成 `素材分段计划.json` 时，使用 `fine_timed_storyboards`。

## 字级时间对齐

素材搜索前必须先完成“字级时间轴 → 语义分段”。这一步的产物是 `分镜时间对齐表.md`，用户确认后才能继续写搜索词和调用素材 API。

- 必须读取 `*_cut_subtitles_words.json`。它包含每个字/词的 `text`、`start`、`end`、`isGap`。
- `isGap == true` 只用于判断停顿和边界，不作为口播正文。
- AI 按原顺序把非 gap 字/词组合成能读通的连续语义段。
- 每段必须记录 `word_start_idx` 和 `word_end_idx`，且索引范围必须连续。
- 每段 `source_start` 取第一个非 gap 字/词的 `start`，`source_end` 取最后一个非 gap 字/词的 `end`。
- 禁止手填时间、估算时间、用 SRT 行时间替代字级时间、或用文稿段落时间猜测。
- 如果用户提供文稿，文稿只用于理解语义和修正 `source_text` 的可读性；时间仍以字级时间轴为唯一来源。
- `source_text` 不能从半个词/半句话开始或结束，例如“和委但只要...”“家里人的阻”这类边界必须回到字级时间轴重切。

## 正式分镜计划

- 先运行 `scripts/start_project.js`，再运行它返回的 `commands.make_seed`。
- 从 `分镜规划输入.json` 读取 cue 列表作辅助预览，不凭记忆重建时间轴。
- 读取完整 `*_cut_subtitles.srt` 和 `*_cut_subtitles_words.json`，其中字级 JSON 是时间来源。
- 按语义组织分镜，保留字幕顺序，覆盖完整剪后时间线。
- 一个素材点只能对应连续字级范围，不能跨段拼接。
- 一个素材点最长 10 秒；遇到主题、动作、主体、转折或画面意图变化时提前切。
- 短分镜可以存在；如果几个短句属于同一个画面意图，可以合并，但必须仍然连续、可读、且不超过 10 秒。

每个素材点字段：

```json
{
  "number": 1,
  "word_start_idx": 12,
  "word_end_idx": 48,
  "source_start": 0.0,
  "source_end": 6.84,
  "source_text": "对应原文",
  "summary": "素材点总结",
  "visual_direction": "横屏画面建议",
  "keywords": ["中文关键词"],
  "search_options": {
    "direct": {"query": "people checking smartphone", "reason": "直接呈现主体和动作"},
    "comprehensive": {"query": "online discussion information conflict", "reason": "同时覆盖直接和关联语义"},
    "associative": {"query": "crowd debating with phones", "reason": "表现相同主题或关系"},
    "metaphorical": {"query": "tangled arrows confusion", "reason": "用清晰意象表达结构或情绪"}
  },
  "priority": "high"
}
```

Priority:

- `high`: 开头钩子、核心论点、关键转折、必须准确呈现的地点/事件/物体/动作。
- `medium`: 重要解释，可接受接近替代画面。
- `low`: 氛围、转场、抽象隐喻。

计划文件顶层必须包含：

```json
{
  "title": "文稿标题",
  "mode": "fine_timed_storyboards",
  "search_strategy": "adaptive_visual_intent",
  "chapters": []
}
```

同时写 `总清单.md` 给用户确认。`总清单.md` 至少包含每个素材点的序号、时间范围、口播原文、画面方向、优先级和四类搜索词。用户确认前不调用 Pexels/Pixabay。
