# API Setup

本 skill 只使用素材平台 API key：

- `PEXELS_API_KEY`
- `PIXABAY_API_KEY`

不要在项目文件、计划 JSON、审核结果或示例文档里写入真实 key。Windows 默认使用用户级环境变量：

先运行初始化检查；依赖通过后再配置 key：

```powershell
node "$SKILL_DIR\scripts\doctor.js" --deps-only --json
node "$SKILL_DIR\scripts\doctor.js" --json
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\set_api_keys.ps1"
```

macOS/Linux 使用当前 shell 或用户 profile 设置：

```bash
export PEXELS_API_KEY="your_pexels_key"
export PIXABAY_API_KEY="your_pixabay_key"
```

检查当前 bundle 和 key：

```powershell
node "$SKILL_DIR\scripts\check_bundle.js" --bundle "$BUNDLE_DIR" --check-keys
```

火山引擎 `VOLCENGINE_API_KEY` 属于 `koubo-audio-video-maker`，不要在本 skill 中引导或保存。
