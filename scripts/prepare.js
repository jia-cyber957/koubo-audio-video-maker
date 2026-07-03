#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const {
  readJson, writeJson, alignPoints, buildDefaultSequence, parseSrt, findFiles, discoverPointDirectories,
} = require('./assembler');

function argsToObject(args) {
  const result = {};
  for (let i = 0; i < args.length; i += 2) result[args[i].replace(/^--/, '')] = args[i + 1];
  return result;
}

function requireOne(root, suffix) {
  const matches = findFiles(root, (_, name) => name.endsWith(suffix));
  if (matches.length !== 1) throw new Error(`需要且只能有一个 *${suffix}，实际找到 ${matches.length} 个`);
  return path.resolve(matches[0]);
}

function main() {
  const args = argsToObject(process.argv.slice(2));
  if (!args.bundle || !args.materials || !args.out) {
    throw new Error('用法: node prepare.js --bundle <目录> --materials <目录> --out <目录>');
  }
  const bundle = path.resolve(args.bundle);
  const materials = path.resolve(args.materials);
  const out = path.resolve(args.out);
  fs.mkdirSync(out, { recursive: true });

  const wavPath = requireOne(bundle, '_cut.wav');
  const srtPath = requireOne(bundle, '_cut_subtitles.srt');
  const wordsPath = requireOne(bundle, '_cut_subtitles_words.json');
  const timelinePath = requireOne(bundle, '_cut_timeline.json');
  const planPath = path.join(materials, '素材分段计划.json');
  const selectionPath = path.join(materials, '素材选择结果.json');
  if (!fs.existsSync(planPath) || !fs.existsSync(selectionPath)) throw new Error('素材目录缺少素材分段计划.json或素材选择结果.json');

  const plan = readJson(planPath);
  const selection = readJson(selectionPath);
  const words = readJson(wordsPath);
  const timeline = readJson(timelinePath);
  const pointDirs = discoverPointDirectories(materials);
  const plannedPoints = [];
  let globalIndex = 0;
  for (const chapter of plan.chapters) {
    for (const point of chapter.material_points) {
      globalIndex += 1;
      plannedPoints.push({
        point_id: String(globalIndex).padStart(3, '0'),
        chapter_title: chapter.chapter_title,
        source_text: point.source_text,
        summary: point.summary,
      });
    }
  }

  const mappingPath = path.join(out, '素材时间对应.json');
  const existingMappings = fs.existsSync(mappingPath) ? readJson(mappingPath).points || [] : [];
  const mappings = alignPoints(plannedPoints, words, existingMappings);
  writeJson(mappingPath, { schema_version: 1, points: mappings });

  const cues = parseSrt(fs.readFileSync(srtPath, 'utf8'));
  const unresolved = mappings.filter(item => !Number.isFinite(item.start) || !Number.isFinite(item.end));
  writeJson(path.join(out, '对齐任务.json'), {
    schema_version: 1,
    instructions: 'Only fill unresolved point start/end using the continuous subtitle cue range. Preserve exact matches.',
    unresolved: unresolved.map(item => ({ ...plannedPoints.find(point => point.point_id === item.point_id), ...item })),
    subtitle_cues: cues,
  });

  const selectionMap = new Map(selection.points.map(point => [String(point.point_id), point]));
  const mappingMap = new Map(mappings.map(item => [String(item.point_id), item]));
  const dataPoints = plannedPoints.map(point => {
    const selected = selectionMap.get(point.point_id) || { downloads: [] };
    const pointDir = pointDirs.get(point.point_id);
    const candidates = selected.downloads.map(download => {
      const filePath = pointDir ? path.join(pointDir, download.file) : '';
      if (!filePath || !fs.existsSync(filePath)) throw new Error(`找不到素材点 ${point.point_id} 的 ${download.file}`);
      return { ...download, candidate_id: String(download.candidate_id), file_path: path.resolve(filePath), duration: Number(download.duration) };
    });
    const mapping = mappingMap.get(point.point_id);
    const start = mapping && Number.isFinite(mapping.start) ? Number(mapping.start) : null;
    const end = mapping && Number.isFinite(mapping.end) ? Number(mapping.end) : null;
    return {
      ...point,
      start,
      end,
      match_method: mapping ? mapping.match_method : 'unresolved',
      candidates,
      defaultSequence: start !== null && end !== null ? buildDefaultSequence(candidates) : [],
    };
  });

  const data = {
    schema_version: 1,
    title: plan.title,
    audio: { path: wavPath, duration: Number(timeline.outputDuration) },
    subtitles: { srt_path: srtPath, words_path: wordsPath },
    inputs: { bundle, materials, timeline_path: timelinePath },
    points: dataPoints,
  };
  writeJson(path.join(out, '装配数据.json'), data);

  const statePath = path.join(out, '装配选择.json');
  if (!fs.existsSync(statePath)) {
    writeJson(statePath, {
      schema_version: 1,
      title: data.title,
      points: data.points.map(point => ({ point_id: point.point_id, disabled: false, sequence: point.defaultSequence })),
    });
  }
  console.log(JSON.stringify({ out, points: dataPoints.length, unresolved: unresolved.length }, null, 2));
  if (unresolved.length) process.exitCode = 2;
}

try { main(); } catch (error) { console.error(error.message); process.exit(1); }
