#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFileSync } = require('child_process');

function parseArgs(argv) {
  const args = { checkKeys: false, json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--bundle') {
      args.bundle = argv[++i];
    } else if (arg === '--extract-to') {
      args.extractTo = argv[++i];
    } else if (arg === '--check-keys') {
      args.checkKeys = true;
    } else if (arg === '--json') {
      args.json = true;
    } else if (arg === '--help' || arg === '-h') {
      args.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

function usage() {
  return [
    'Usage: node check_bundle.js --bundle <剪口播bundle解压目录或zip> [--extract-to <目录>] [--check-keys] [--json]',
    '',
    'The bundle directory must contain:',
    '  *_cut.wav',
    '  *_cut_timeline.json',
    '  *_cut_subtitles_words.json',
    '  *_cut_subtitles.srt',
  ].join('\n');
}

function findFiles(root, predicate, output = []) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) findFiles(full, predicate, output);
    else if (predicate(full, entry.name)) output.push(full);
  }
  return output;
}

function findBySuffix(dir, suffix) {
  return findFiles(dir, (_, name) => name.endsWith(suffix));
}

function safeBaseName(filePath) {
  return path.basename(filePath).replace(/\.zip$/i, '').replace(/[<>:"/\\|?*\x00-\x1F]/g, '_');
}

function extractZip(zipPath, targetRoot) {
  const target = path.resolve(targetRoot || path.join(path.dirname(zipPath), safeBaseName(zipPath)));
  fs.mkdirSync(target, { recursive: true });
  if (process.platform === 'win32') {
    execFileSync('powershell.exe', [
      '-NoProfile',
      '-Command',
      '& { param($zip, $dest) Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force }',
      zipPath,
      target,
    ], { stdio: 'pipe', windowsHide: true });
  } else {
    execFileSync('unzip', ['-o', zipPath, '-d', target], { stdio: 'pipe' });
  }
  return target;
}

function collapseBundleRoot(root) {
  const direct = validateFiles(root, false);
  if (direct.ok) return root;
  const dirs = fs.readdirSync(root, { withFileTypes: true }).filter(entry => entry.isDirectory());
  if (dirs.length === 1) {
    const child = path.join(root, dirs[0].name);
    const childFiles = validateFiles(child, false);
    if (childFiles.ok) return child;
  }
  return root;
}

function readUserEnvOnWindows(name) {
  if (process.platform !== 'win32') return '';
  try {
    const { execFileSync } = require('child_process');
    const output = execFileSync('powershell.exe', [
      '-NoProfile',
      '-Command',
      `[Environment]::GetEnvironmentVariable('${name}','User')`,
    ], { encoding: 'utf8', windowsHide: true });
    return output.trim();
  } catch {
    return '';
  }
}

function envValue(name) {
  return (process.env[name] || readUserEnvOnWindows(name) || '').trim();
}

function validateFiles(bundlePath, strict = true) {
  const required = [
    ['wav', '_cut.wav'],
    ['timeline', '_cut_timeline.json'],
    ['words', '_cut_subtitles_words.json'],
    ['srt', '_cut_subtitles.srt'],
  ];
  const files = {};
  const missing = [];
  const duplicates = [];
  for (const [key, suffix] of required) {
    const matches = findBySuffix(bundlePath, suffix);
    if (matches.length === 0) missing.push(suffix);
    else if (matches.length > 1) duplicates.push({ suffix, matches });
    else files[key] = path.resolve(matches[0]);
  }
  if (!strict) return { ok: missing.length === 0 && duplicates.length === 0, files, missing, duplicates };
  if (missing.length) throw new Error(`Bundle is missing required files: ${missing.join(', ')}`);
  if (duplicates.length) {
    throw new Error(duplicates.map(item => [
      `Bundle has multiple *${item.suffix} files:`,
      ...item.matches.map(match => `  ${match}`),
    ].join(os.EOL)).join(os.EOL));
  }
  return { ok: true, files, missing, duplicates };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  if (!args.bundle) throw new Error(`Missing --bundle\n\n${usage()}`);

  let bundlePath = path.resolve(args.bundle);
  if (!fs.existsSync(bundlePath)) throw new Error(`Bundle path does not exist: ${bundlePath}`);
  const stat = fs.statSync(bundlePath);
  let extractedTo = null;
  if (!stat.isDirectory()) {
    if (bundlePath.toLowerCase().endsWith('.zip')) {
      extractedTo = extractZip(bundlePath, args.extractTo);
      bundlePath = collapseBundleRoot(extractedTo);
    } else {
      throw new Error(`Bundle path is not a directory or zip: ${bundlePath}`);
    }
  }

  const validation = validateFiles(bundlePath, true);

  let keyState = null;
  if (args.checkKeys) {
    const missingKeys = ['PEXELS_API_KEY', 'PIXABAY_API_KEY'].filter((name) => !envValue(name));
    keyState = { ok: missingKeys.length === 0, missing: missingKeys };
    if (missingKeys.length) {
      const script = path.join(path.dirname(__filename), 'set_api_keys.ps1');
      throw new Error([
        `Missing API keys: ${missingKeys.join(', ')}`,
        'Run:',
        `powershell -NoProfile -ExecutionPolicy Bypass -File "${script}"`,
      ].join('\n'));
    }
  }

  console.log(JSON.stringify({
    ok: true,
    bundle: bundlePath,
    extracted_to: extractedTo,
    files: validation.files,
    keys: keyState,
  }, null, 2));
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
