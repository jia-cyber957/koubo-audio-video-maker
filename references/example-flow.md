# Example Flow

用户给出：

```text
C:\output\demo_cut_bundle.zip
```

第一步：

```powershell
node "$SKILL_DIR\scripts\start_project.js" --bundle "C:\output\demo_cut_bundle.zip" --title "demo" --work-dir "C:\koubo-work" --check-keys
```

入口脚本输出：

- `bundle_dir`: 解压后的 bundle 目录
- `material_project`: 素材项目目录，最终包含 `素材分段计划.json` 和 `素材选择结果.json`
- `assembly_dir`: 装配项目目录
- `commands.make_seed`: 生成 `分镜规划输入.json`
- `commands.status`: 素材状态机下一步查询命令

执行 `commands.make_seed` 后，AI 读取 `分镜规划输入.json`，写：

- `素材分段计划.json`
- `总清单.md`

用户确认 `总清单.md` 后，执行状态机直到下载完成，再执行：

```powershell
node "$SKILL_DIR\scripts\prepare.js" --bundle "<bundle_dir>" --materials "<material_project>" --out "<assembly_dir>"
node "$SKILL_DIR\scripts\validate.js" "<assembly_dir>"
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\serve_review.ps1" -ProjectDir "<assembly_dir>"
```

最后在审片页导出 MP4 成片。
