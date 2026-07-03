'use strict';

const fs = require('fs');
const path = require('path');

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function writeJson(file, value) {
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

function normalizeText(text) {
  return String(text || '').normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}]/gu, '');
}

function buildTranscript(words) {
  let text = '';
  const charWords = [];
  for (const word of words) {
    if (!word || word.isGap || !word.text) continue;
    const normalized = normalizeText(word.text);
    for (const char of normalized) {
      text += char;
      charWords.push(word);
    }
  }
  return { text, charWords };
}

function alignPoints(points, words, existingMappings = []) {
  const transcript = buildTranscript(words);
  const existing = new Map(existingMappings.map(item => [String(item.point_id), item]));
  const mappings = [];
  let cursor = 0;

  for (const point of points) {
    const pointId = String(point.point_id);
    const manual = existing.get(pointId);
    if (manual && Number.isFinite(Number(manual.start)) && Number.isFinite(Number(manual.end)) && Number(manual.end) > Number(manual.start)) {
      mappings.push({ ...manual, point_id: pointId, start: Number(manual.start), end: Number(manual.end) });
      continue;
    }

    const needle = normalizeText(point.source_text);
    const index = needle ? transcript.text.indexOf(needle, cursor) : -1;
    if (index >= 0) {
      const first = transcript.charWords[index];
      const last = transcript.charWords[index + needle.length - 1];
      mappings.push({ point_id: pointId, start: first.start, end: last.end, match_method: 'exact', reason: 'normalized exact text match' });
      cursor = index + needle.length;
    } else {
      mappings.push({ point_id: pointId, start: null, end: null, match_method: 'unresolved', reason: 'exact text not found' });
    }
  }
  return mappings;
}

function buildDefaultSequence(candidates) {
  const sorted = [...candidates].sort((a, b) => b.score - a.score || a.candidate_id.localeCompare(b.candidate_id));
  return sorted.length ? [sorted[0].candidate_id] : [];
}

function recalculatePoint(point, selection) {
  const duration = Math.max(0, point.end - point.start);
  const candidateMap = new Map(point.candidates.map(candidate => [candidate.candidate_id, candidate]));
  const disabled = Boolean(selection && selection.disabled);
  const sequence = disabled ? [] : (selection && Array.isArray(selection.sequence) ? selection.sequence : point.defaultSequence);
  const cleanSequence = sequence.filter(id => candidateMap.has(id));
  const usedSequence = [];
  const slots = [];
  let cursor = point.start;

  for (const candidateId of cleanSequence) {
    if (cursor >= point.end - 1e-6) break;
    const candidate = candidateMap.get(candidateId);
    const slotDuration = Math.min(Number(candidate.duration) || 0, point.end - cursor);
    if (slotDuration <= 0) continue;
    usedSequence.push(candidateId);
    slots.push({
      candidate_id: candidateId,
      timeline_start: cursor,
      timeline_end: cursor + slotDuration,
      duration: slotDuration,
      source_start: 0,
    });
    cursor += slotDuration;
  }

  return {
    point_id: point.point_id,
    disabled,
    sequence: usedSequence,
    slots,
    uncovered_duration: Math.max(0, point.end - cursor),
  };
}

function buildCalculatedState(data, state) {
  const selections = new Map(((state && state.points) || []).map(item => [String(item.point_id), item]));
  return {
    schema_version: 1,
    title: data.title,
    updated_at: new Date().toISOString(),
    points: data.points.map(point => recalculatePoint(point, selections.get(String(point.point_id)))),
  };
}

function parseSrt(content) {
  const timeToSeconds = value => {
    const match = value.match(/(\d+):(\d+):(\d+),(\d+)/);
    if (!match) return null;
    return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
  };
  return String(content).trim().split(/\r?\n\r?\n/).map(block => {
    const lines = block.split(/\r?\n/);
    const times = (lines[1] || '').split(' --> ');
    return { index: Number(lines[0]), start: timeToSeconds(times[0] || ''), end: timeToSeconds(times[1] || ''), text: lines.slice(2).join(' ') };
  }).filter(cue => Number.isFinite(cue.start) && Number.isFinite(cue.end));
}

function findFiles(root, predicate, output = []) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) findFiles(full, predicate, output);
    else if (predicate(full, entry.name)) output.push(full);
  }
  return output;
}

function discoverPointDirectories(materialRoot) {
  const result = new Map();
  for (const note of findFiles(materialRoot, (_, name) => name === '说明.md')) {
    const firstLine = fs.readFileSync(note, 'utf8').split(/\r?\n/, 1)[0];
    const match = firstLine.match(/^#\s+(\d+)\s*\//);
    if (match) result.set(match[1].padStart(3, '0'), path.dirname(note));
  }
  return result;
}

module.exports = {
  readJson,
  writeJson,
  normalizeText,
  buildTranscript,
  alignPoints,
  buildDefaultSequence,
  recalculatePoint,
  buildCalculatedState,
  parseSrt,
  findFiles,
  discoverPointDirectories,
};
