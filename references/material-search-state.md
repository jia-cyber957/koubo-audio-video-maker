# Material Search State Machine

使用 `scripts/download_materials.py`，不要绕开状态机。

推荐优先使用 `start_project.js` 返回的 `python` 和 `commands`。手动运行时可用以下形式：

```powershell
$python = "python"  # Windows 也可用 py；macOS/Linux 通常用 python3
$script = "$SKILL_DIR\scripts\download_materials.py"
$plan = "<素材项目目录>\素材分段计划.json"
$out = "<outputs目录>"

& $python $script validate --plan-path $plan --output-root $out
& $python $script status --plan-path $plan --output-root $out
```

不要硬编码 Python312 路径；让入口脚本探测，或按平台选择 `python` / `py` / `python3`。

只执行 `status` 返回的 `next_command`。可出现的命令包括：

```powershell
& $python $script collect --round 1 --plan-path $plan --output-root $out
& $python $script prepare-queries --round 6 --plan-path $plan --output-root $out
& $python $script finalize-review --round 1 --plan-path $plan --output-root $out
& $python $script review-check --round 1 --plan-path $plan --output-root $out
& $python $script download --plan-path $plan --output-root $out
```

搜索规则：

- 第 1 轮 Pexels direct。
- 第 2 轮 Pixabay direct。
- 第 3 轮 comprehensive，两平台。
- 第 4 轮 associative，两平台。
- 第 5 轮 metaphorical，两平台。
- 第 6 轮才让 AI 根据失败结果写新查询。

停止条件：一个素材点有至少 3 个 `55+` 候选，且至少 1 个 `75+` 候选。第 6 轮后仍不足也停止，保留可用候选，但不下载 `55` 以下素材。

审核规则：

- `文字审核结果.json` 由 LLM/Codex 填写，按口播文本、搜索词、候选标题/tags/元数据的语义相关性亲自打分。代码不得自动计算语义分。
- 不做视觉审核，候选只按文本/关键词语义评分；不要读取缩略图、不要生成 contact sheet、不要把 `needs_visual_review` 写成 true。
- 候选池按点统计：至少 3 个 `55+`，且至少 1 个 `75+`。满足后该点不再补轮次；未满足的点继续进入下一轮补缺。
- `finalize-review` 只把 LLM/Codex 已经打好的文字评分转成 `AI审核结果.json`，代码只校验/转写/按阈值标记 decision，不得重新打分，不得引入视觉观察。
- 只有 `review-check` 可以创建 `审核完成.json`。

## 流程单

每个项目必须维护 `流程单.md`，每完成一步就把 `[ ]` 改成 `[x]`，并写明产物路径或下一步。每轮最少包含：

- [ ] `status` 返回本轮下一步
- [ ] `collect --round N` 只采集未达标点
- [ ] LLM/Codex 亲自填写 `文字审核结果.json` 的 score 和 reason
- [ ] `finalize-review --round N` 按 LLM 评分生成无视觉版 `AI审核结果.json`，代码不打语义分
- [ ] `review-check --round N` 冻结审核
- [ ] 逐点统计：3 个 `55+` 且 1 个 `75+` 是否满足
- [ ] 达标点停止；未达标点进入下一轮

失败规则：

- 缺 key、401/403/429、网络失败、下载超时或下载失败时停止当前命令。
- 下载默认分批执行：`download` 每次最多处理 10 个素材点，相当于默认 `--batch-size 10`。这是上限，不是必须数量；最后一批剩 1-9 个也必须正常完成。
- 如果 `素材选择结果.json` 里的 `download_batch.remaining_points > 0`，继续重跑同一个 `download --plan-path ... --output-root ...` 命令，直到 `download_batch.complete == true` 或 `status` 返回 complete。
- 下载恢复必须重跑同一个 `download --plan-path ... --output-root ...` 命令；脚本会跳过已经存在且非空的 `video_XX.mp4`，只补缺失文件。`.part` 半成品不是完成文件，会被清理后重下。
- 读取 `运行状态.json` 和 `运行日志.txt`，修复后重跑同一个命令。
- 普通恢复不使用 `--force`，不要删除已下载目录，不要从第 1 个点覆盖重下。


