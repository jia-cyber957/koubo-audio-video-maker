'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const WIDTH = 1920;
const HEIGHT = 1080;
const FPS = 30;

function firstExisting(paths) {
  return paths.find(file => file && fs.existsSync(file)) || null;
}

function findFfmpeg() {
  if (process.env.FFMPEG_PATH && fs.existsSync(process.env.FFMPEG_PATH)) return path.resolve(process.env.FFMPEG_PATH);
  const locator = process.platform === 'win32' ? 'where.exe' : 'which';
  const located = spawnSync(locator, ['ffmpeg'], { encoding: 'utf8', windowsHide: true });
  if (located.status === 0) {
    const found = String(located.stdout || '').split(/\r?\n/).map(item => item.trim()).find(Boolean);
    if (found && fs.existsSync(found)) return path.resolve(found);
  }
  if (process.platform !== 'win32') return null;
  const local = process.env.LOCALAPPDATA || '';
  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const direct = firstExisting([
    path.join(local, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
    path.join(local, 'Programs', 'ffmpeg', 'bin', 'ffmpeg.exe'),
    path.join(programFiles, 'ffmpeg', 'bin', 'ffmpeg.exe'),
  ]);
  if (direct) return path.resolve(direct);
  const packages = path.join(local, 'Microsoft', 'WinGet', 'Packages');
  if (!fs.existsSync(packages)) return null;
  const stack = [packages];
  while (stack.length) {
    const dir = stack.pop();
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { continue; }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name.toLowerCase() === 'ffmpeg.exe') return path.resolve(full);
    }
  }
  return null;
}

function buildTimeline(data, state) {
  const total = Number(data.audio.duration);
  const candidateMap = new Map();
  for (const point of data.points) for (const candidate of point.candidates) candidateMap.set(String(candidate.candidate_id), candidate);
  const slots = [];
  for (const point of state.points) {
    for (const slot of point.slots || []) {
      const candidate = candidateMap.get(String(slot.candidate_id));
      if (!candidate) throw new Error(`找不到候选素材: ${slot.candidate_id}`);
      const start = Math.max(0, Number(slot.timeline_start));
      const end = Math.min(total, Number(slot.timeline_end));
      if (end > start + 0.0005) slots.push({
        type: 'video', start, end, duration: end - start,
        sourceStart: Math.max(0, Number(slot.source_start) || 0), file: candidate.file_path,
      });
    }
  }
  slots.sort((a, b) => a.start - b.start || a.end - b.end);
  const timeline = [];
  let cursor = 0;
  for (const slot of slots) {
    if (slot.start > cursor + 0.0005) timeline.push({ type: 'blank', start: cursor, end: slot.start, duration: slot.start - cursor });
    if (slot.start < cursor - 0.0005) throw new Error(`视频片段时间重叠: ${slot.start.toFixed(3)}s`);
    timeline.push(slot);
    cursor = slot.end;
  }
  if (cursor < total - 0.0005) timeline.push({ type: 'blank', start: cursor, end: total, duration: total - cursor });
  if (!timeline.length && total > 0) timeline.push({ type: 'blank', start: 0, end: total, duration: total });
  return timeline;
}

function ffNumber(value) {
  return Number(value).toFixed(6).replace(/0+$/, '').replace(/\.$/, '') || '0';
}

function buildRenderSpec(data, state, projectDir) {
  const timeline = buildTimeline(data, state);
  const title = `${data.title}_assembled`;
  const outputPath = path.join(projectDir, `${title}.mp4`);
  const partPath = path.join(projectDir, `${title}.part.mp4`);
  const filterPath = path.join(projectDir, `.${title}.filter.txt`);
  const args = ['-hide_banner', '-y'];
  const filters = [];
  const labels = [];
  let inputIndex = 0;
  for (let i = 0; i < timeline.length; i += 1) {
    const segment = timeline[i];
    const label = `v${i}`;
    labels.push(`[${label}]`);
    if (segment.type === 'video') {
      args.push('-ss', ffNumber(segment.sourceStart), '-t', ffNumber(segment.duration), '-i', segment.file);
      filters.push(`[${inputIndex}:v:0]trim=duration=${ffNumber(segment.duration)},setpts=PTS-STARTPTS,scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1,fps=${FPS},format=yuv420p[${label}]`);
      inputIndex += 1;
    } else {
      filters.push(`color=c=black:s=${WIDTH}x${HEIGHT}:r=${FPS}:d=${ffNumber(segment.duration)},format=yuv420p[${label}]`);
    }
  }
  args.push('-i', data.audio.path);
  filters.push(`${labels.join('')}concat=n=${labels.length}:v=1:a=0[outv]`);
  filters.push(`[${inputIndex}:a:0]aresample=48000,apad,atrim=duration=${ffNumber(data.audio.duration)},asetpts=PTS-STARTPTS[outa]`);
  args.push(
    '-filter_complex_script', filterPath,
    '-map', '[outv]', '-map', '[outa]',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', String(FPS),
    '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
    '-t', ffNumber(data.audio.duration), '-movflags', '+faststart',
    '-progress', 'pipe:1', '-nostats', partPath,
  );
  return { timeline, outputPath, partPath, filterPath, filterText: filters.join(';\n'), args, total: Number(data.audio.duration) };
}

function parseProgressTime(line) {
  const match = String(line).match(/^out_time=(\d+):(\d+):(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

class RenderManager {
  constructor(projectDir, options = {}) {
    this.projectDir = path.resolve(projectDir);
    this.ffmpegPath = options.ffmpegPath === undefined ? findFfmpeg() : options.ffmpegPath;
    this.spawnImpl = options.spawnImpl || spawn;
    this.statusPath = path.join(this.projectDir, 'mp4_render_status.json');
    this.status = { state: 'idle', percent: 0, processed_seconds: 0, total_seconds: 0, ffmpeg_path: this.ffmpegPath };
    this.child = null;
  }

  saveStatus(patch) {
    this.status = { ...this.status, ...patch, updated_at: new Date().toISOString() };
    fs.writeFileSync(this.statusPath, JSON.stringify(this.status, null, 2), 'utf8');
  }

  getStatus() { return { ...this.status }; }

  start(data, state) {
    if (this.child || ['preparing', 'rendering'].includes(this.status.state)) throw new Error('已有MP4正在合成，请等待完成');
    if (!this.ffmpegPath || !fs.existsSync(this.ffmpegPath)) {
      throw new Error('未找到 ffmpeg。请先安装 ffmpeg，或设置 FFMPEG_PATH 后重新启动审核服务。');
    }
    const spec = buildRenderSpec(data, state, this.projectDir);
    if (fs.existsSync(spec.partPath)) fs.rmSync(spec.partPath, { force: true });
    fs.writeFileSync(spec.filterPath, spec.filterText, 'utf8');
    this.saveStatus({
      state: 'preparing', percent: 0, processed_seconds: 0, total_seconds: spec.total,
      output_path: spec.outputPath, error: null, log_tail: [], started_at: new Date().toISOString(), finished_at: null,
    });
    const logs = [];
    let stdoutBuffer = '';
    const child = this.spawnImpl(this.ffmpegPath, spec.args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.child = child;
    this.saveStatus({ state: 'rendering' });
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      stdoutBuffer += chunk;
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop() || '';
      for (const line of lines) {
        const seconds = parseProgressTime(line.trim());
        if (seconds === null) continue;
        const processed = Math.max(0, Math.min(spec.total, seconds));
        this.saveStatus({ processed_seconds: processed, percent: Math.max(0, Math.min(99.9, processed / spec.total * 100)) });
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => {
      logs.push(...String(chunk).split(/\r?\n/).filter(Boolean));
      if (logs.length > 40) logs.splice(0, logs.length - 40);
    });
    child.once('error', error => this.finishFailure(error, spec, logs));
    child.once('close', code => {
      if (!this.child) return;
      if (code === 0 && fs.existsSync(spec.partPath)) {
        try {
          if (fs.existsSync(spec.outputPath)) fs.rmSync(spec.outputPath, { force: true });
          fs.renameSync(spec.partPath, spec.outputPath);
          this.child = null;
          try { fs.rmSync(spec.filterPath, { force: true }); } catch (_) {}
          this.saveStatus({ state: 'completed', percent: 100, processed_seconds: spec.total, finished_at: new Date().toISOString(), log_tail: logs });
        } catch (error) { this.finishFailure(error, spec, logs); }
      } else {
        this.finishFailure(new Error(`ffmpeg 退出码 ${code}`), spec, logs);
      }
    });
    return this.getStatus();
  }

  finishFailure(error, spec, logs) {
    if (!this.child && this.status.state === 'failed') return;
    this.child = null;
    try { fs.rmSync(spec.partPath, { force: true }); } catch (_) {}
    try { fs.rmSync(spec.filterPath, { force: true }); } catch (_) {}
    this.saveStatus({ state: 'failed', error: error.message, finished_at: new Date().toISOString(), log_tail: logs.slice(-40) });
  }
}

module.exports = { WIDTH, HEIGHT, FPS, findFfmpeg, buildTimeline, buildRenderSpec, parseProgressTime, RenderManager };
