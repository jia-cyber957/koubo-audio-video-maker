#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--transcribe-dir') args.transcribeDir = argv[++i];
    else if (arg === '--title') args.title = argv[++i];
    else if (arg === '--out') args.out = argv[++i];
    else if (arg === '--ffmpeg') args.ffmpeg = argv[++i];
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return args;
}

function usage() {
  return 'Usage: node make_audio_bundle.js --transcribe-dir <项目/1_转录> --title <标题> --out <bundle目录> [--ffmpeg <ffmpeg>]';
}

function safeName(value) {
  return String(value || 'audio')
    .normalize('NFKC')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'audio';
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function writeJson(file, value) {
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

function pad(number, length = 2) {
  return String(Math.floor(number)).padStart(length, '0');
}

function formatTime(seconds) {
  const msTotal = Math.max(0, Math.round(Number(seconds || 0) * 1000));
  const ms = msTotal % 1000;
  const totalSeconds = Math.floor(msTotal / 1000);
  const s = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const m = totalMinutes % 60;
  const h = Math.floor(totalMinutes / 60);
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms, 3)}`;
}

function wordsOnly(words) {
  return words.filter(word => word && !word.isGap && String(word.text || '').trim());
}

function buildSrt(words) {
  const real = wordsOnly(words);
  const cues = [];
  let current = [];
  const shouldBreakAfter = text => /[。！？!?；;]/.test(String(text || ''));
  for (const word of real) {
    if (!current.length) {
      current.push(word);
      continue;
    }
    const previous = current[current.length - 1];
    const gap = Number(word.start) - Number(previous.end);
    const text = current.map(item => item.text).join('');
    if (gap >= 0.45 || text.length >= 22 || shouldBreakAfter(previous.text)) {
      cues.push(current);
      current = [];
    }
    current.push(word);
  }
  if (current.length) cues.push(current);
  return cues.map((cue, index) => {
    const start = cue[0].start;
    const end = cue[cue.length - 1].end;
    const text = cue.map(word => word.text).join('');
    return `${index + 1}\n${formatTime(start)} --> ${formatTime(end)}\n${text}`;
  }).join('\n\n') + '\n';
}

function findFfmpeg(explicit) {
  const candidates = [explicit, process.env.FFMPEG_PATH, 'ffmpeg.exe', 'ffmpeg'].filter(Boolean);
  for (const candidate of candidates) {
    const result = spawnSync(candidate, ['-version'], { encoding: 'utf8', windowsHide: true });
    if (result.status === 0) return candidate;
  }
  return '';
}

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  if (!args.transcribeDir || !args.title || !args.out) throw new Error(usage());

  const transcribeDir = path.resolve(args.transcribeDir);
  const out = path.resolve(args.out);
  const title = safeName(args.title);
  const audioMp3 = path.join(transcribeDir, 'audio.mp3');
  const wordsPath = path.join(transcribeDir, 'subtitles_words.json');
  const volcPath = path.join(transcribeDir, 'volcengine_v3_result.json');
  for (const file of [audioMp3, wordsPath]) {
    if (!fs.existsSync(file)) throw new Error(`Required transcribe output not found: ${file}`);
  }
  fs.mkdirSync(out, { recursive: true });

  const words = readJson(wordsPath);
  const realWords = wordsOnly(words);
  if (!realWords.length) throw new Error('subtitles_words.json has no spoken words');
  const duration = Math.max(...realWords.map(word => Number(word.end) || 0));

  const cutMp3 = path.join(out, `${title}_cut.mp3`);
  const cutWav = path.join(out, `${title}_cut.wav`);
  const cutWords = path.join(out, `${title}_cut_subtitles_words.json`);
  const cutSrt = path.join(out, `${title}_cut_subtitles.srt`);
  const timelinePath = path.join(out, `${title}_cut_timeline.json`);
  const handoffPath = path.join(out, 'handoff_to_koubo_video_assembler.md');

  copyFile(audioMp3, cutMp3);
  const ffmpeg = findFfmpeg(args.ffmpeg);
  if (ffmpeg) {
    const result = spawnSync(ffmpeg, ['-y', '-i', audioMp3, '-vn', '-ac', '2', '-ar', '48000', cutWav], {
      encoding: 'utf8',
      windowsHide: true,
    });
    if (result.status !== 0) throw new Error(`ffmpeg failed to create wav: ${(result.stderr || result.stdout || '').trim()}`);
  } else {
    copyFile(audioMp3, cutWav);
  }

  writeJson(cutWords, words);
  fs.writeFileSync(cutSrt, buildSrt(words), 'utf8');
  writeJson(timelinePath, {
    schema_version: 1,
    mode: 'identity_from_raw_audio',
    sourceDuration: duration,
    outputDuration: duration,
    segments: [{ sourceStart: 0, sourceEnd: duration, outputStart: 0, outputEnd: duration }],
    note: 'Input audio was not cut; output timeline equals source timeline.',
  });
  if (fs.existsSync(volcPath)) copyFile(volcPath, path.join(out, 'volcengine_v3_result.json'));
  fs.writeFileSync(handoffPath, [
    '# koubo-video-assembler handoff',
    '',
    `- Audio source: ${audioMp3}`,
    `- Bundle directory: ${out}`,
    `- Words: ${cutWords}`,
    `- SRT: ${cutSrt}`,
    `- Timeline: ${timelinePath}`,
    '',
    'This bundle was generated directly from raw audio transcription. No mouth-slip cutting was performed.',
  ].join('\n'), 'utf8');

  console.log(JSON.stringify({
    ok: true,
    bundle: out,
    files: { mp3: cutMp3, wav: cutWav, words: cutWords, srt: cutSrt, timeline: timelinePath, handoff: handoffPath },
  }, null, 2));
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
