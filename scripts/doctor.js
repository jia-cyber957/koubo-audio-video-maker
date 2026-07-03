#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync, execFileSync } = require('child_process');

const args = process.argv.slice(2);
const DEPS_ONLY = args.includes('--deps-only');
const JSON_OUT = args.includes('--json');

const isWin = process.platform === 'win32';

function uniq(values) {
  const seen = new Set();
  return values.filter(value => {
    if (!value) return false;
    const key = typeof value === 'string'
      ? value.trim().toLowerCase().replace(/[\\\/]+$/, '')
      : JSON.stringify(value).toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function pathDirsFromEnv(...names) {
  const dirs = [];
  for (const name of names) {
    const value = process.env[name];
    if (!value) continue;
    if (/\.(exe|cmd|bat)$/i.test(value)) dirs.push(path.dirname(value));
    else dirs.push(value);
  }
  return dirs;
}

function isWindowsAppsAlias(candidatePath) {
  return isWin && /\\WindowsApps\\/i.test(String(candidatePath || ''));
}

function whereCommand(command) {
  const locator = isWin ? 'where.exe' : 'which';
  try {
    const result = spawnSync(locator, [command], { encoding: 'utf8', timeout: 5000, windowsHide: true });
    if (result.status !== 0) return [];
    return String(result.stdout || '').split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  } catch {
    return [];
  }
}

function commandResult(candidate, commandArgs = ['--version']) {
  const command = typeof candidate === 'string' ? candidate : candidate.command;
  const args = typeof candidate === 'string' ? commandArgs : (candidate.args || commandArgs);
  const label = typeof candidate === 'string' ? candidate : (candidate.label || candidate.command);
  if (!command) return { ok: false, label, error: 'empty candidate' };

  const hasPath = /[\\/]/.test(command);
  if (hasPath && !fs.existsSync(command)) return { ok: false, label, command, error: 'path not found' };
  if (isWindowsAppsAlias(command)) return { ok: false, label, command, error: 'skipped WindowsApps alias' };

  if (!hasPath) {
    const resolved = whereCommand(command);
    if (resolved.length && resolved.every(isWindowsAppsAlias)) {
      return { ok: false, label, command, resolved, error: 'skipped WindowsApps alias' };
    }
  }

  try {
    const result = spawnSync(command, args, { encoding: 'utf8', timeout: 5000, windowsHide: true });
    if (result.status === 0) {
      const resolved = hasPath ? [command] : whereCommand(command).filter(p => !isWindowsAppsAlias(p));
      return { ok: true, label, command, args, path: resolved[0] || command };
    }
    return {
      ok: false, label, command, args, status: result.status, signal: result.signal,
      error: result.error ? result.error.message : String(result.stderr || result.stdout || 'non-zero exit').trim(),
    };
  } catch (error) {
    return { ok: false, label, command, args, error: error.message };
  }
}

function firstCommand(candidates, commandArgs = ['--version']) {
  const failures = [];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const result = commandResult(candidate, commandArgs);
    if (result.ok) return result;
    failures.push(result);
  }
  return { ok: false, failures };
}

function readUserEnvOnWindows(name) {
  if (process.platform !== 'win32') return '';
  try {
    return execFileSync('powershell.exe', [
      '-NoProfile',
      '-Command',
      `[Environment]::GetEnvironmentVariable('${name}','User')`,
    ], { encoding: 'utf8', windowsHide: true }).trim();
  } catch {
    return '';
  }
}

function envValue(name) {
  return (process.env[name] || readUserEnvOnWindows(name) || '').trim();
}

function scanPythonInstallDirs() {
  if (!isWin) return [];
  const roots = [
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python'),
    path.join(process.env.ProgramFiles || '', 'Python'),
    path.join(process.env['ProgramFiles(x86)'] || '', 'Python'),
  ];
  const found = [];
  for (const root of roots) {
    try {
      for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory() || !/^Python\d+/i.test(entry.name)) continue;
        found.push(path.join(root, entry.name, 'python.exe'));
      }
    } catch {}
  }
  return found.sort().reverse();
}

function pythonCandidates() {
  const explicit = [
    process.env.PYTHON_PATH,
    process.env.PYTHON,
    process.env.PYTHON3,
    ...pathDirsFromEnv('SKILL_LOCAL_BIN', 'CODEX_WORKSPACE_BIN').map(dir => path.join(dir, isWin ? 'python.exe' : 'python')),
    ...pathDirsFromEnv('SKILL_LOCAL_BIN', 'CODEX_WORKSPACE_BIN').map(dir => path.join(dir, isWin ? 'python3.exe' : 'python3')),
  ];
  if (!isWin) return uniq([...explicit, 'python3', 'python']);
  return uniq([
    ...explicit,
    ...scanPythonInstallDirs(),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe'),
    { label: 'py -3', command: 'py', args: ['-3', '--version'] },
    'python',
    'py',
    'python3',
  ]);
}

function ffmpegCandidates() {
  const skillDir = path.resolve(__dirname, '..');
  const binDirs = uniq([
    ...pathDirsFromEnv('SKILL_LOCAL_BIN', 'CODEX_WORKSPACE_BIN'),
    path.join(skillDir, 'work', 'bin'),
    path.join(path.dirname(skillDir), 'work', 'bin'),
    path.join(process.cwd(), 'work', 'bin'),
  ]);
  const candidates = [
    process.env.FFMPEG_PATH,
    ...binDirs.map(dir => path.join(dir, isWin ? 'ffmpeg.exe' : 'ffmpeg')),
  ];
  if (isWin) {
    candidates.push(
      path.join(process.env.LOCALAPPDATA || '', 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
      path.join(process.env.LOCALAPPDATA || '', 'Programs', 'ffmpeg', 'bin', 'ffmpeg.exe'),
      path.join(process.env.ProgramFiles || '', 'ffmpeg', 'bin', 'ffmpeg.exe'),
      'ffmpeg',
    );
  } else {
    candidates.push('ffmpeg');
  }
  return uniq(candidates);
}

function checkDeps() {
  const python = firstCommand(pythonCandidates(), ['--version']);
  const ffmpeg = firstCommand(ffmpegCandidates(), ['-version']);
  const powershell = isWin ? firstCommand(['powershell.exe'], ['-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()']) : { ok: true, path: 'not-required' };
  const unzip = isWin ? { ok: true, path: 'not-required' } : firstCommand(['unzip'], ['-v']);
  const deps = {
    node: process.execPath,
    python: python.path || '',
    ffmpeg: ffmpeg.path || '',
    powershell: powershell.path || '',
    unzip: unzip.path || '',
  };
  const errors = {
    python: python.ok ? [] : python.failures || [],
    ffmpeg: ffmpeg.ok ? [] : ffmpeg.failures || [],
    powershell: powershell.ok ? [] : powershell.failures || [],
    unzip: unzip.ok ? [] : unzip.failures || [],
  };
  const missing = [];
  if (!python.ok) missing.push('python/python3');
  if (!ffmpeg.ok) missing.push('ffmpeg');
  if (isWin && !powershell.ok) missing.push('powershell');
  if (!isWin && !unzip.ok) missing.push('unzip');
  return { ok: missing.length === 0, missing, deps, errors };
}

function checkKeys() {
  const missing = ['PEXELS_API_KEY', 'PIXABAY_API_KEY'].filter(name => !envValue(name));
  return { ok: missing.length === 0, missing };
}

function printHuman(deps, keys) {
  console.log('\n[koubo-video-assembler doctor]');
  console.log('\n[1/2] 本地依赖');
  if (deps.ok) {
    console.log('  OK: Node / Python / ffmpeg / 解压工具可用');
  } else {
    console.log(`  缺少: ${deps.missing.join(', ')}`);
    console.log('  Windows 建议: 先运行 koubo-audio-video-maker/scripts/install_ffmpeg.ps1；Python 请安装 3.10+');
    console.log('  macOS 建议: brew install ffmpeg python unzip');
  }
  console.log(`  python: ${deps.deps.python || 'missing'}`);
  console.log(`  ffmpeg: ${deps.deps.ffmpeg || 'missing'}`);

  if (DEPS_ONLY) return;
  console.log('\n[2/2] 素材 API Key');
  if (keys.ok) {
    console.log('  OK: PEXELS_API_KEY / PIXABAY_API_KEY 已找到');
  } else {
    console.log(`  缺少: ${keys.missing.join(', ')}`);
    if (process.platform === 'win32') {
      console.log(`  运行: powershell -NoProfile -ExecutionPolicy Bypass -File "${path.join(__dirname, 'set_api_keys.ps1')}"`);
    } else {
      console.log('  运行: export PEXELS_API_KEY="your_pexels_key"');
      console.log('  运行: export PIXABAY_API_KEY="your_pixabay_key"');
    }
  }
}

const deps = checkDeps();
const keys = DEPS_ONLY ? { ok: true, missing: [] } : checkKeys();
printHuman(deps, keys);

if (JSON_OUT) {
  console.log('__DOCTOR_JSON__ ' + JSON.stringify({
    ok: deps.ok && keys.ok,
    depsOk: deps.ok,
    missingDeps: deps.missing,
    deps: deps.deps,
    dependencyErrors: deps.errors,
    keysOk: keys.ok,
    missingKeys: keys.missing,
  }));
}

process.exit(deps.ok && keys.ok ? 0 : 1);
