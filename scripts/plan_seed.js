#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--bundle') args.bundle = argv[++i];
    else if (arg === '--title') args.title = argv[++i];
    else if (arg === '--out') args.out = argv[++i];
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return args;
}

function usage() {
  return 'Usage: node plan_seed.js --bundle <bundle目录> --title <标题> --out <素材项目目录>';
}

function findFiles(root, predicate, output = []) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) findFiles(full, predicate, output);
    else if (predicate(full, entry.name)) output.push(full);
  }
  return output;
}

function requireOne(root, suffix) {
  const matches = findFiles(root, (_, name) => name.endsWith(suffix));
  if (matches.length !== 1) throw new Error(`需要且只能有一个 *${suffix}，实际找到 ${matches.length} 个`);
  return path.resolve(matches[0]);
}

function parseSrt(content) {
  const timeToSeconds = value => {
    const match = String(value || '').match(/(\d+):(\d+):(\d+),(\d+)/);
    if (!match) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
  };
  return String(content).trim().split(/\r?\n\r?\n/).map(block => {
    const lines = block.split(/\r?\n/);
    const times = (lines[1] || '').split(' --> ');
    return {
      index: Number(lines[0]),
      start: timeToSeconds(times[0]),
      end: timeToSeconds(times[1]),
      text: lines.slice(2).join(' ').trim(),
    };
  }).filter(cue => Number.isFinite(cue.start) && Number.isFinite(cue.end) && cue.text);
}

function makeDraftPlan(title, cues) {
  return {
    title,
    mode: 'fine_timed_storyboards',
    search_strategy: 'adaptive_visual_intent',
    chapters: [{
      chapter_number: 1,
      chapter_title: '第一幕',
      chapter_summary: '待根据口播内容总结',
      source_text_range: cues.length ? `${cues[0].start.toFixed(2)}-${cues[cues.length - 1].end.toFixed(2)}` : '',
      material_points: [],
    }],
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  if (!args.bundle || !args.title || !args.out) throw new Error(usage());

  const bundle = path.resolve(args.bundle);
  const out = path.resolve(args.out);
  fs.mkdirSync(out, { recursive: true });
  const srtPath = requireOne(bundle, '_cut_subtitles.srt');
  const wordsPath = requireOne(bundle, '_cut_subtitles_words.json');
  const cues = parseSrt(fs.readFileSync(srtPath, 'utf8'));
  const seed = {
    schema_version: 1,
    title: args.title,
    bundle,
    srt_path: srtPath,
    words_path: wordsPath,
    rules: [
      'Use adjacent cues only.',
      'Every storyboard must be <= 10.0 seconds.',
      'Cover the full spoken timeline in chronological order.',
      'Write 素材分段计划.json using fine_timed_storyboards and adaptive_visual_intent.',
    ],
    cues,
    draft_plan: makeDraftPlan(args.title, cues),
  };
  const seedPath = path.join(out, '分镜规划输入.json');
  const draftPath = path.join(out, '素材分段计划.draft.json');
  const listPath = path.join(out, '总清单.md');
  fs.writeFileSync(seedPath, JSON.stringify(seed, null, 2), 'utf8');
  fs.writeFileSync(draftPath, JSON.stringify(seed.draft_plan, null, 2), 'utf8');
  fs.writeFileSync(listPath, [
    `# ${args.title} 素材分镜清单`,
    '',
    '等待 AI 根据 `分镜规划输入.json` 填写 `素材分段计划.json` 后，再给用户确认。',
    '',
    `- 字幕条数: ${cues.length}`,
    cues.length ? `- 时间范围: ${cues[0].start.toFixed(2)}s - ${cues[cues.length - 1].end.toFixed(2)}s` : '- 时间范围: 空',
  ].join('\n'), 'utf8');
  console.log(JSON.stringify({ ok: true, seed: seedPath, draft_plan: draftPath, checklist: listPath, cues: cues.length }, null, 2));
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
