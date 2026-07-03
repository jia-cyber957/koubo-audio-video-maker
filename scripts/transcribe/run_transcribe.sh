#!/bin/bash
#
# 步骤 0-4 自动化流水线
# 用法: ./run_transcribe.sh <video.mp4|audio_dir> [base_output_dir] [--engine]
#
# 引擎选项（默认 auto 轮流）:
#   （无）/--auto 每次在 flash / 标准版 间交替，分摊两份各 20h 免费额度 ≈ 共 40h
#                 （需在控制台同时开通极速版 auc_turbo 与标准版 auc 两个资源）
#   --flash       只用极速版 auc_turbo（一次直出、最快；只开了一个资源时用这个）
#   --v3-standard 只用标准版 auc（异步 submit/query 轮询）
#
# 输出: base_output_dir/1_转录/
#   ├── audio.mp3
#   ├── volcengine_v3_result.json
#   └── subtitles_words.json
#

set -e

MEDIA_INPUT="$1"
BASE_DIR="${2:-.}"
ENGINE="auto"  # 默认 flash / 标准版 轮流，吃满两份免费额度

# 检测引擎参数（任意位置）
for arg in "$@"; do
  case "$arg" in
    --v3-standard) ENGINE="v3-standard" ;;
    --flash)       ENGINE="flash" ;;
    --auto)        ENGINE="auto" ;;
  esac
done

if [ -z "$MEDIA_INPUT" ]; then
  echo "用法: $0 <video.mp4|audio_dir> [base_output_dir] [--flash|--v3-standard]"
  exit 1
fi

if [ ! -f "$MEDIA_INPUT" ] && [ ! -d "$MEDIA_INPUT" ]; then
  echo "❌ 媒体文件/目录不存在: $MEDIA_INPUT"
  exit 1
fi

# 依赖预检
for cmd in ffmpeg node python3 curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ 缺少依赖: $cmd"
    case "$cmd" in
      ffmpeg) echo "   macOS: brew install ffmpeg" ;;
      node)   echo "   macOS: brew install node" ;;
    esac
    exit 1
  fi
done

SKILL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TRANSCRIBE_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONUTF8=1  # 让子进程 python 用 UTF-8，避免中文路径/日志在某些 locale 下乱码

# --auto：在 flash / 标准版 间轮流，让两份各 20h 的免费额度都被消耗（共 ≈40h）
# 注意：本次选了哪个引擎，要等转录【成功】后才写入 .engine_toggle，
# 否则失败的运行也会白白切换引擎（下次又轮到另一个，免费额度分摊就乱了）。
TOGGLE_STATE=""
if [ "$ENGINE" = "auto" ]; then
  STATE="$SKILL_DIR/.engine_toggle"
  [ "$(cat "$STATE" 2>/dev/null)" = "flash" ] && ENGINE="v3-standard" || ENGINE="flash"
  TOGGLE_STATE="$STATE"
  echo "🔄 auto 轮流：本次用 $ENGINE"
fi

TRANSCRIBE_DIR="$BASE_DIR/1_转录"
mkdir -p "$TRANSCRIBE_DIR"

is_supported_media_file() {
  case "${1##*.}" in
    mp3|MP3|wav|WAV|wave|WAVE|m4a|M4A|aac|AAC|flac|FLAC|ogg|OGG|opus|OPUS|mp4|MP4|mov|MOV|m4v|M4V) return 0 ;;
    *) return 1 ;;
  esac
}

collect_media_parts() {
  local input="$1"
  MEDIA_PARTS=()
  if [ -d "$input" ]; then
    while IFS= read -r -d '' file; do
      MEDIA_PARTS+=("$file")
    done < <(
      find "$input" -maxdepth 1 -type f \
        \( -iname '*.mp3' -o -iname '*.wav' -o -iname '*.wave' -o -iname '*.m4a' -o -iname '*.aac' -o -iname '*.flac' -o -iname '*.ogg' -o -iname '*.opus' -o -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' \) \
        -print0 | sort -z -V
    )
  elif is_supported_media_file "$input"; then
    MEDIA_PARTS=("$input")
  fi
}

# ── 步骤 1: 提取音频 ────────────────────────────────────
echo "📦 步骤1: 提取音频..."
collect_media_parts "$MEDIA_INPUT"
if [ "${#MEDIA_PARTS[@]}" -eq 0 ]; then
  echo "❌ 没有找到可处理的媒体文件: $MEDIA_INPUT"
  echo "   支持: mp3/wav/m4a/aac/flac/ogg/opus/mp4/mov/m4v"
  exit 1
fi

if [ "${#MEDIA_PARTS[@]}" -eq 1 ]; then
  echo "   单个媒体: ${MEDIA_PARTS[0]}"
  ffmpeg -i "${MEDIA_PARTS[0]}" -vn -acodec libmp3lame -y "$TRANSCRIBE_DIR/audio.mp3" 2>/dev/null
else
  echo "   检测到 ${#MEDIA_PARTS[@]} 段媒体，将按文件名自然顺序合并："
  printf '     %s\n' "${MEDIA_PARTS[@]}"
  FFMPEG_ARGS=()
  FILTER_INPUTS=""
  for idx in "${!MEDIA_PARTS[@]}"; do
    FFMPEG_ARGS+=(-i "${MEDIA_PARTS[$idx]}")
    FILTER_INPUTS+="[$idx:a]"
  done
  ffmpeg "${FFMPEG_ARGS[@]}" -filter_complex "${FILTER_INPUTS}concat=n=${#MEDIA_PARTS[@]}:v=0:a=1[outa]" -map "[outa]" -acodec libmp3lame -y "$TRANSCRIBE_DIR/audio.mp3" 2>/dev/null
fi
echo "✅ 音频已保存: $TRANSCRIBE_DIR/audio.mp3"

# ── 步骤 2+3: 转录 ─────────────────────────────────────
echo "🚀 步骤2+3: 转录（引擎: $ENGINE）..."

case "$ENGINE" in
  flash)
    bash "$TRANSCRIBE_SCRIPT_DIR/volcengine_flash_transcribe.sh" "$TRANSCRIBE_DIR/audio.mp3" "$TRANSCRIBE_DIR"
    RESULT_FILE="$TRANSCRIBE_DIR/volcengine_v3_result.json"
    ;;
  v3-standard)
    bash "$TRANSCRIBE_SCRIPT_DIR/volcengine_v3_transcribe.sh" "$TRANSCRIBE_DIR/audio.mp3" "$TRANSCRIBE_DIR"
    RESULT_FILE="$TRANSCRIBE_DIR/volcengine_v3_result.json"
    ;;
  *)
    echo "❌ 未知引擎: $ENGINE"
    exit 1
    ;;
esac

echo "✅ 步骤2+3 完成"

# 转录成功后才记录本次用的引擎（auto 模式下次轮到另一个）；失败时 set -e 已提前退出，不会切换
[ -n "$TOGGLE_STATE" ] && echo "$ENGINE" > "$TOGGLE_STATE"

# ── 步骤 4: 生成字幕 ───────────────────────────────────
echo "📝 步骤4: 生成字幕..."
node "$TRANSCRIBE_SCRIPT_DIR/generate_subtitles.js" \
  "$RESULT_FILE" \
  "" \
  "$TRANSCRIBE_DIR"

echo ""
echo "🎉 流水线完成！"
echo "   输出目录: $TRANSCRIBE_DIR"
ls -lh "$TRANSCRIBE_DIR"/*.mp3 "$TRANSCRIBE_DIR"/*.json 2>/dev/null | awk '{print "     "$9"  "$5}'

