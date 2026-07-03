#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { readJson, buildCalculatedState } = require('./assembler');

function validate(projectDir) {
  const dataPath = path.join(projectDir, '装配数据.json');
  const statePath = path.join(projectDir, '装配选择.json');
  if (!fs.existsSync(dataPath) || !fs.existsSync(statePath)) throw new Error('缺少装配数据.json或装配选择.json');
  const data = readJson(dataPath);
  const state = readJson(statePath);
  if (!fs.existsSync(data.audio.path) || !fs.existsSync(data.subtitles.srt_path)) throw new Error('音频或SRT文件不存在');
  const unresolved = data.points.filter(point => !Number.isFinite(point.start) || !Number.isFinite(point.end) || point.end <= point.start);
  if (unresolved.length) throw new Error(`仍有未对应素材点: ${unresolved.map(point => point.point_id).join(', ')}`);
  for (const point of data.points) for (const candidate of point.candidates) if (!fs.existsSync(candidate.file_path)) throw new Error(`素材不存在: ${candidate.file_path}`);
  const calculated = buildCalculatedState(data, state);
  for (const point of calculated.points) {
    const source = data.points.find(item => item.point_id === point.point_id);
    if (point.slots.some(slot => slot.timeline_start < source.start - 1e-6 || slot.timeline_end > source.end + 1e-6)) throw new Error(`素材点 ${point.point_id} 时间越界`);
  }
  return { data, calculated };
}

if (require.main === module) {
  try {
    const projectDir = path.resolve(process.argv[2] || '.');
    const result = validate(projectDir);
    console.log(JSON.stringify({ valid: true, points: result.data.points.length }, null, 2));
  } catch (error) { console.error(error.message); process.exit(1); }
}

module.exports = { validate };
