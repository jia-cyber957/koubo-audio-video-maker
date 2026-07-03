---
name: koubo-audio-video-maker
description: 输入一个纯音频文件，独立完成火山引擎转录、字级时间轴、10 秒内流畅语义分镜、Pexels/Pixabay 多轮补缺搜索、LLM 文本关键词评分、素材下载、装配审片、MP4 成片导出。用于纯音频口播配画面、口播成片、找视频素材并装配；不要要求用户先运行其他 skill 或先提供任何外部 bundle；已有音频字幕 bundle 只作为迁移兼容入口。
---

# 单音频口播视频成片

从一个**纯音频文件**直接开始，完成“火山引擎转录 → 字级时间轴 → 10 秒内流畅语义分段 → 分镜 → 素材搜索 → LLM 文本关键词评分 → 素材下载 → 装配审片 → MP4 成片导出”。不要把它当成某个前置流程的附属步骤；不要要求用户先运行其他 skill。

默认入口就是单音频。内部会把火山转录结果桥接成后续脚本需要的 bundle 结构，这是本 skill 自己生成的中间产物，不是用户输入前提。已有 bundle 入口只用于迁移旧项目。

## Inputs

推荐输入：

- 一个纯音频文件：`.wav`、`.mp3`、`.m4a`、`.aac`、`.flac`、`.ogg`、`.opus`。入口脚本使用本 skill 内置的火山转录脚本生成字级时间轴，并把原音频包装成标准 bundle。

可选迁移输入：

- 已有音频字幕 bundle 解压目录或 `*_cut_bundle.zip`，只用于接续旧项目；不能因为用户给了音频就反过来要求这个 bundle。若使用它，需要包含：
  - `*_cut.wav`
  - `*_cut_timeline.json`
  - `*_cut_subtitles_words.json`
  - `*_cut_subtitles.srt`

需要的 key：

- `VOLCENGINE_API_KEY`：仅单音频入口需要，用于火山转录。查找顺序为环境变量、本 skill 的 `.env`、上一级 `.env`。
- `PEXELS_API_KEY` 和 `PIXABAY_API_KEY`：素材搜索需要。

## Workflow

0. 先初始化检查。每次开始先检查本地依赖；依赖通过后再检查 Pexels/Pixabay key：

```powershell
node "$SKILL_DIR\scripts\doctor.js" --deps-only --json
node "$SKILL_DIR\scripts\doctor.js" --json
```

如果缺依赖，先按 doctor 输出安装 Node/Python/ffmpeg/解压工具；如果使用单音频入口，还要按 `commands.transcribe_doctor` 检查火山转录依赖和 `VOLCENGINE_API_KEY`。素材搜索 key 仍由本 skill 的 `set_api_keys.ps1` 配置。

Windows/Codex 注意事项：winget 新装 Node/Python/ffmpeg 后，当前 Codex 或 PowerShell 会话的 PATH 可能不会立刻刷新，重开 Codex/终端通常会恢复。`doctor.js` 会优先寻找真实 `python.exe` 和 `ffmpeg.exe`，跳过 `WindowsApps` 的 `python3` 占位 alias；不要手工维护临时 `python3.cmd` shim。若 ffmpeg 已由任一本地修复脚本安装过，doctor 会优先识别 `FFMPEG_PATH`、`SKILL_LOCAL_BIN`、`CODEX_WORKSPACE_BIN` 或 `work/bin` 里的稳定副本。

Windows 上 ffmpeg 缺失时，优先使用本 skill 自带的转录修复脚本，然后重跑本 skill 的依赖检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\transcribe\install_ffmpeg.ps1"
node "$SKILL_DIR\scripts\doctor.js" --deps-only --json
```

素材 API key 配置：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\set_api_keys.ps1"
```

1. 建项目并检查输入。优先使用单音频入口；入口脚本会返回火山转录、生成 bundle、分镜 seed、素材搜索状态机和装配的完整命令。

单音频入口：

```powershell
node "$SKILL_DIR\scripts\start_project.js" --audio "<纯音频文件>" --title "<项目标题>" --work-dir "<工作目录>" --engine auto --check-keys
```

可选迁移 bundle 入口：

```powershell
node "$SKILL_DIR\scripts\start_project.js" --bundle "<bundle目录或zip>" --title "<项目标题>" --work-dir "<工作目录>" --check-keys
```

如果使用 `--audio`，必须按入口返回的顺序先跑：`transcribe_doctor` → `transcribe_check` → `transcribe` → `make_audio_bundle` → `check_bundle`。`make_audio_bundle` 只做格式桥接：原音频不剪辑，timeline 是 identity 映射，字级时间来自火山。

2. 执行入口脚本返回的 `commands.make_seed`。它会生成 `分镜规划输入.json`、`素材分段计划.draft.json` 和初始 `总清单.md`。

3. **先做“字级时间轴 → 语义分段”**，这是素材搜索前的硬门槛。必须读取 bundle 里的 `*_cut_subtitles_words.json`，确认它包含每个字/词的 `text`、`start`、`end` 和 `isGap`。不要直接把 SRT cue 的碎片文本当成分镜文案；SRT cue 只是辅助预览，真正的分段和时间必须来自字级时间轴。

   分段方式：
   - 按原顺序读取所有 `isGap != true` 的字/词，AI 先把它们组合成能读通的连续语义段。
   - 每个语义段必须来自连续的字级元素范围，记录 `word_start_idx` 和 `word_end_idx`。
   - 每段的 `source_start` 必须等于该段第一个非 gap 字/词的 `start`，`source_end` 必须等于该段最后一个非 gap 字/词的 `end`。禁止手填、估算或用文稿时间猜。
   - 单段 `source_end - source_start <= 10.0`。如果一句话超过 10 秒，按自然语义边界拆；如果几个短碎句属于同一个画面意图，可以合并，但仍必须连续且不超过 10 秒。
   - `source_text` 必须是这段口播的可读文本，不能出现从半个词/半句话开始或结束的碎片，例如“和委但只要…”、“力也好我们…”这类边界必须重切。
   - 如果用户提供文稿，文稿只能用于纠正可读文本和理解语义；时间仍以字级时间轴为唯一来源。

   本步必须先输出并让用户确认：
   - `分镜时间对齐表.md`：列出每段编号、`word_start_idx-word_end_idx`、`source_start-source_end`、时长、可读口播文本。
   - `素材分段计划.json`：在每个素材点中保留 `word_start_idx`、`word_end_idx`、`source_start`、`source_end`、`source_text`。

4. 在用户确认 `分镜时间对齐表.md` 之后，再把草稿补成正式 `素材分段计划.json`，并更新 `总清单.md` 给用户预览。不要在用户确认分镜计划前调用素材 API。

5. 分镜计划必须使用 `mode: "fine_timed_storyboards"` 和 `search_strategy: "adaptive_visual_intent"`：
   - 每个素材点来自连续字级时间范围，按原时间顺序覆盖完整剪后音频。
   - 单个素材点 `source_end - source_start <= 10.0`。
   - 以语义边界切分，不机械每 10 秒切一次。
   - 可读文本必须先读通，再写搜索词；禁止直接使用 ASR/SRT 的断字碎片生成素材点。
   - 每个素材点包含直接、综合、联想、意象四类英文搜索词。
   - 不做视觉审核，候选只按文本/关键词语义评分，达到阈值即进入下一步。评分必须由 LLM/Codex 根据口播文本、搜索关键词、候选标题/tags/元数据亲自判断；代码只校验和汇总，不得自动计算语义分。不要读取缩略图、不要生成 contact sheet、不要把 `needs_visual_review` 写成 true。
   - 评分门槛按素材点候选池统计：至少 3 个 `55+`，且至少 1 个 `75+`。满足后该点不再补轮次；未满足的点才进入下一轮补缺。

详细分镜和搜索策略见 [references/storyboard-search.md](references/storyboard-search.md)。

6. 用户确认计划后，先创建并持续更新 `流程单.md`。每完成一个动作就把对应项改成 `[x]`，并补一句实际产物/下一步；未打勾的步骤不得跳过。最低清单：

   - [ ] validate：校验 `素材分段计划.json`
   - [ ] status：读取唯一下一步
   - [ ] collect round N：只采集本轮符合资格的点
   - [ ] AI text review round N：LLM/Codex 按口播文本、搜索词和候选标题/tags/元数据亲自打分
   - [ ] finalize-review round N：代码只把 LLM 文字评分转成最终审核，不做视觉审核、不重新打分
   - [ ] review-check round N：冻结本轮审核
   - [ ] threshold check：逐点统计是否已有至少 3 个 `55+` 且至少 1 个 `75+`
   - [ ] next round decision：达标点停止；未达标点才进入下一轮补缺
   - [ ] download batch N：全部达标或第 6 轮结束后下载可用候选；默认每次最多下载 10 个素材点，最后一批可以少于 10 个；若还有剩余或下载超时/中断，重跑同一个 download 命令，脚本必须跳过已存在且非空的 `video_XX.mp4`，只补未完成文件

7. 用户确认计划后，运行状态机。优先使用入口脚本返回的 `commands.validate_plan` 和 `commands.status`。每次只执行 `status` 输出的 `next_command`，每个操作后再次运行 `status`：

```powershell
$python = "<入口脚本返回的 python>"
$script = "$SKILL_DIR\scripts\download_materials.py"
$plan = "<入口脚本返回的 plan_path>"
$out = "<入口脚本返回的 outputs_root>"

& $python $script validate --plan-path $plan --output-root $out
& $python $script status --plan-path $plan --output-root $out
```

素材审核、六轮搜索、失败恢复规则见 [references/material-search-state.md](references/material-search-state.md)。不要手写或伪造 `审核完成.json`，不要做视觉审核，不要在状态机允许前下载 MP4。

8. 下载阶段必须分批且可恢复。`download` 默认相当于 `--batch-size 10`，每次最多处理 10 个素材点，不要求凑满 10 个；最后一批剩 1-9 个也必须正常完成。每次运行都会写入/更新同一个 `素材选择结果.json`。如果 `download_batch.remaining_points > 0`，必须重跑同一个 `download --plan-path ... --output-root ...` 命令继续下一批。脚本会跳过已经存在且文件大小大于 0 的 `video_XX.mp4`，只下载缺失文件；中断留下的 `.part` 半成品会被清理后重下。不要删除素材目录、不要改 `素材分段计划.json`、不要加 `--force`、不要为了“重试”从第 1 个素材点覆盖重下。只有 `download_batch.complete == true` 或 `status` 返回 complete 后，才进入装配。

9. 下载完成后，使用入口脚本返回的 `commands.prepare_assembly` 准备装配项目：

```powershell
node "$SKILL_DIR\scripts\prepare.js" --bundle "$BUNDLE_DIR" --materials "<素材项目目录>" --out "<装配目录>"
```

读取 `<装配目录>/对齐任务.json`。若 `unresolved` 不为空，只为未解决项填写 `<装配目录>/素材时间对应.json` 中的 `start`、`end`、`match_method: "ai"` 和 `reason`，再重新运行 `prepare.js`。不要改动已精确匹配项。

10. 校验并启动装配审片页：

```powershell
node "$SKILL_DIR\scripts\validate.js" "<装配目录>"
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\serve_review.ps1" -ProjectDir "<装配目录>"
```

用户在审片页替换候选、留空或恢复默认后，网页只导出 MP4 成片：
- `*_assembled.mp4`
- `*_subtitles.srt`
- `装配选择.json`
- `装配检查报告.json`

装配契约见 [references/assembly-contract.md](references/assembly-contract.md)。
完整跑法示例见 [references/example-flow.md](references/example-flow.md)。

## Rules

- 单音频入口可以火山转录并读取 `VOLCENGINE_API_KEY`；可选迁移 bundle 入口不得重复转录。两种入口都只负责转写、分镜、找视频和装配，不做口误剪辑。
- 不全自动跑到底：分镜计划、素材审核、装配审片都是人工确认点。
- 分镜时间必须来自 `*_cut_subtitles_words.json` 的字级 `start/end`；禁止用 SRT 行断点、文稿段落、手填秒数或估算时间替代。
- `source_text` 必须是完整可读的语义段。发现半词、半句、ASR 断裂文本时，必须回到字级时间轴重新分段，而不是继续搜索素材。
- 搜素材前必须先生成并确认 `分镜时间对齐表.md`。
- 默认目录由 `start_project.js` 决定，不手写临时目录；用户另有要求时用 `--work-dir`。
- 素材视频原声必须关闭；MP4 不烧录字幕。
- 未解决的素材时间对应必须阻止启动审片和导出。


