#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { readJson, writeJson, buildCalculatedState } = require('./assembler');
const { RenderManager } = require('./renderer');
const { validate } = require('./validate');

const htmlPath = path.join(__dirname, '..', 'assets', 'review.html');

function sendJson(res, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': body.length, 'Cache-Control': 'no-store' });
  res.end(body);
}

function bodyJson(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => {
      body += chunk;
      if (body.length > 2_000_000) reject(new Error('请求过大'));
    });
    req.on('end', () => {
      try { resolve(JSON.parse(body || '{}')); } catch (error) { reject(error); }
    });
  });
}

function sendFile(req, res, file, contentType, options = {}) {
  if (!fs.existsSync(file)) { res.writeHead(404); res.end('Not Found'); return; }
  const stat = fs.statSync(file);
  const range = req.headers.range;
  const cacheControl = options.cacheControl || 'no-store';
  const commonHeaders = {
    'Content-Type': contentType,
    'Accept-Ranges': 'bytes',
    'Cache-Control': cacheControl,
  };
  if (range) {
    const [startText, endText] = range.replace('bytes=', '').split('-');
    const start = Math.max(0, Number(startText) || 0);
    const end = Math.min(endText ? Number(endText) : stat.size - 1, stat.size - 1);
    if (start > end || start >= stat.size) {
      res.writeHead(416, { 'Content-Range': `bytes */${stat.size}`, 'Cache-Control': cacheControl });
      res.end();
      return;
    }
    res.writeHead(206, {
      ...commonHeaders,
      'Content-Range': `bytes ${start}-${end}/${stat.size}`,
      'Content-Length': end - start + 1,
    });
    if (req.method === 'HEAD') { res.end(); return; }
    fs.createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { ...commonHeaders, 'Content-Length': stat.size });
    if (req.method === 'HEAD') { res.end(); return; }
    fs.createReadStream(file).pipe(res);
  }
}

function sendDownload(req, res, file, contentType) {
  if (!fs.existsSync(file)) { res.writeHead(404); res.end('Not Found'); return; }
  const name = path.basename(file).replace(/["\r\n]/g, '_');
  res.setHeader('Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(name)}`);
  return sendFile(req, res, file, contentType);
}

function writeExportArtifacts(projectDir, data, calculated) {
  const statePath = path.join(projectDir, '装配选择.json');
  writeJson(statePath, calculated);
  const srtOutput = path.join(projectDir, `${data.title}_subtitles.srt`);
  fs.copyFileSync(data.subtitles.srt_path, srtOutput);
  const report = {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    audio_duration: data.audio.duration,
    points: calculated.points.map(point => ({
      point_id: point.point_id,
      slots: point.slots.length,
      uncovered_duration: point.uncovered_duration,
      disabled: point.disabled,
    })),
  };
  const reportPath = path.join(projectDir, '装配检查报告.json');
  writeJson(reportPath, report);
  return { statePath, srtOutput, reportPath };
}

function validateIncomingState(data, incoming) {
  if (!incoming || !Array.isArray(incoming.points)) throw new Error('points 必须是数组');
  const pointMap = new Map(data.points.map(point => [point.point_id, point]));
  const seen = new Set();
  for (const point of incoming.points) {
    const source = pointMap.get(String(point.point_id));
    if (!source || seen.has(source.point_id)) throw new Error(`无效或重复素材点: ${point.point_id}`);
    seen.add(source.point_id);
    if (!Array.isArray(point.sequence)) throw new Error(`${point.point_id}.sequence 必须是数组`);
    const allowed = new Set(source.candidates.map(candidate => candidate.candidate_id));
    for (const id of point.sequence) if (!allowed.has(String(id))) throw new Error(`${point.point_id} 包含未知候选: ${id}`);
  }
  if (seen.size !== data.points.length) throw new Error('素材点数量不完整');
}

function createAssemblerServer(projectDirInput, requestedPort = 0) {
  const projectDir = path.resolve(projectDirInput || '.');
  const dataPath = path.join(projectDir, '装配数据.json');
  const statePath = path.join(projectDir, '装配选择.json');
  const load = () => ({ data: readJson(dataPath), state: readJson(statePath) });
  const renderManager = new RenderManager(projectDir);

  const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  try {
    if ((req.method === 'GET' || req.method === 'HEAD') && url.pathname === '/') return sendFile(req, res, htmlPath, 'text/html; charset=utf-8');
    if (req.method === 'GET' && url.pathname === '/api/project') {
      const { data, state } = load();
      return sendJson(res, 200, { data, state: buildCalculatedState(data, state), render: renderManager.getStatus() });
    }
    if ((req.method === 'GET' || req.method === 'HEAD') && url.pathname === '/api/audio') {
      const { data } = load();
      return sendFile(req, res, data.audio.path, 'audio/wav', { cacheControl: 'private, max-age=3600' });
    }
    if ((req.method === 'GET' || req.method === 'HEAD') && url.pathname === '/api/video') {
      const { data } = load();
      const point = data.points.find(item => item.point_id === url.searchParams.get('point'));
      const candidate = point && point.candidates.find(item => item.candidate_id === url.searchParams.get('candidate'));
      if (!candidate) { res.writeHead(404); res.end('Not Found'); return; }
      return sendFile(req, res, candidate.file_path, 'video/mp4', { cacheControl: 'private, max-age=3600' });
    }
    if (req.method === 'POST' && url.pathname === '/api/state') {
      const { data } = load();
      const incoming = await bodyJson(req);
      validateIncomingState(data, incoming);
      const calculated = buildCalculatedState(data, incoming);
      writeJson(statePath, calculated);
      return sendJson(res, 200, { success: true, state: calculated });
    }
    if (req.method === 'POST' && url.pathname === '/api/render') {
      const { data, calculated } = validate(projectDir);
      const files = writeExportArtifacts(projectDir, data, calculated);
      const status = renderManager.start(data, calculated);
      return sendJson(res, 202, { success: true, status, srt: files.srtOutput, state: files.statePath, report: files.reportPath });
    }
    if (req.method === 'GET' && url.pathname === '/api/render/status') {
      return sendJson(res, 200, { success: true, status: renderManager.getStatus() });
    }
    if (req.method === 'GET' && url.pathname === '/api/render/download') {
      const status = renderManager.getStatus();
      if (status.state !== 'completed' || !status.output_path) return sendJson(res, 409, { success: false, error: 'MP4尚未合成完成' });
      return sendDownload(req, res, status.output_path, 'video/mp4');
    }
    res.writeHead(404); res.end('Not Found');
  } catch (error) {
    sendJson(res, 500, { success: false, error: error.message });
  }
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(Number(requestedPort) || 0, '127.0.0.1', () => {
      const port = server.address().port;
      const url = `http://127.0.0.1:${port}`;
      fs.writeFileSync(path.join(projectDir, 'server_url.txt'), url, 'utf8');
      fs.writeFileSync(path.join(projectDir, '.assembler_server.pid'), String(process.pid), 'utf8');
      resolve({ server, url, port });
    });
  });
}

if (require.main === module) {
  createAssemblerServer(process.argv[2] || '.', Number(process.argv[3] || 0))
    .then(result => console.log(`READY_URL=${result.url}`))
    .catch(error => { console.error(error.message); process.exit(1); });
}

module.exports = { createAssemblerServer, writeExportArtifacts };
