# 安装说明

## 方式一：安装为 Codex skill

把 `koubo-audio-video-maker` 文件夹复制到 Codex skills 目录：

```text
C:\Users\<你的用户名>\.codex\skills\koubo-audio-video-maker
```

安装后，在 Codex 中提出类似请求：

```text
用 koubo-audio-video-maker 给这个口播音频找画面并装配成片
```

## 系统依赖

需要：

- Node.js
- Python 3.10+
- ffmpeg
- curl
- PowerShell，Windows 推荐

运行自检：

```powershell
$SKILL_DIR = "$env:USERPROFILE\.codex\skills\koubo-audio-video-maker"

node "$SKILL_DIR\scripts\doctor.js" --deps-only --json
node "$SKILL_DIR\scripts\transcribe\doctor.js" --deps-only --json
```

如果 Windows 找不到 ffmpeg，可以运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\transcribe\install_ffmpeg.ps1"
```

## API key

需要配置：

```text
VOLCENGINE_API_KEY
PEXELS_API_KEY
PIXABAY_API_KEY
```

推荐放在环境变量或 skill 目录下的 `.env` 中。素材搜索 key 也可以运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$SKILL_DIR\scripts\set_api_keys.ps1"
```

## 创建项目

```powershell
$SKILL_DIR = "$env:USERPROFILE\.codex\skills\koubo-audio-video-maker"

node "$SKILL_DIR\scripts\start_project.js" `
  --audio "D:\audio\narration.mp3" `
  --title "my-koubo-video" `
  --work-dir "D:\koubo-work" `
  --engine auto `
  --check-keys
```

`start_project.js` 会输出一组命令。按顺序执行，不要跳过：

1. `transcribe_doctor`
2. `transcribe_check`
3. `transcribe`
4. `make_audio_bundle`
5. `check_bundle`
6. `make_seed`
7. 让 AI 写并确认 `素材分段计划.json`
8. 跑素材状态机
9. 下载素材
10. 装配审片

