#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function parseArgs(argv) {
  const args = { checkKeys: false, engine: 'auto' };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--audio') args.audio = argv[++i];
    else if (arg === '--bundle') args.bundle = argv[++i];
    else if (arg === '--title') args.title = argv[++i];
    else if (arg === '--work-dir') args.workDir = argv[++i];
    else if (arg === '--engine') args.engine = argv[++i];
    else if (arg === '--check-keys') args.checkKeys = true;
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return args;
}

function usage() {
  return [
    'Usage:',
    '  node start_project.js --audio <纯音频文件> [--title <标题>] [--work-dir <目录>] [--engine auto|flash|v3-standard]',
    '  node start_project.js --bundle <bundle目录或zip> [--title <标题>] [--work-dir <目录>] [--check-keys]',
  ].join('\n');
}

function quote(value) {
  return `"${String(value).replace(/"/g, '\\"')}"`;
}

function safeName(value) {
  return String(value || 'koubo-video')
    .normalize('NFKC')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80) || 'koubo-video';
}

function inferTitle(input, explicitTitle) {
  if (explicitTitle) return safeName(explicitTitle);
  const base = path.basename(input).replace(/\.zip$/i, '').replace(/\.[^.]+$/i, '');
  return safeName(base.replace(/_cut_bundle$/i, '').replace(/_cut$/i, ''));
}

function runNode(script, args) {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: 'utf8', windowsHide: true });
  if (result.status !== 0) throw new Error((result.stderr || result.stdout || '').trim());
  return JSON.parse(result.stdout);
}

function findPythonCommand() {
  const candidates = process.platform === 'win32'
    ? [
      path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe'),
      'python',
      'py',
    ]
    : ['python3', 'python'];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const result = spawnSync(candidate, ['--version'], { encoding: 'utf8', windowsHide: true });
    if (result.status === 0) return candidate;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function writeWorkflowChecklist(checklistPath, title, inputMode) {
  if (fs.existsSync(checklistPath)) return;
  const lines = [
    `# ${title} 流程单`,
    '',
    `- 输入模式：${inputMode}`,
    '- 规则：每完成一步，把 `[ ]` 改成 `[x]`，并在同一行或下一行写产物路径/下一步。未打勾不得跳过。',
    '',
    '## 入口与转录',
    '- [ ] start_project：生成项目目录和命令清单',
    '- [ ] transcribe_doctor：单音频入口检查本地依赖和火山配置；bundle 入口可跳过',
    '- [ ] transcribe_check：单音频入口只检查路径和依赖，不上传；bundle 入口可跳过',
    '- [ ] transcribe：单音频入口上传火山并生成字级时间轴；bundle 入口可跳过',
    '- [ ] make_audio_bundle：单音频入口把转录结果包装为标准 bundle；bundle 入口可跳过',
    '- [ ] check_bundle：确认 bundle 必需文件齐全',
    '',
    '## 分镜',
    '- [ ] make_seed：生成分镜规划输入和草稿',
    '- [ ] AI 按字级时间轴做 10 秒内流畅语义分段',
    '- [ ] 用户确认分镜时间对齐表和素材分段计划',
    '- [ ] validate_plan：校验素材分段计划',
    '',
    '## 素材搜索与审核',
    '- [ ] status：读取唯一下一步',
    '- [ ] collect round N：只采集未达标点',
    '- [ ] LLM/Codex text review round N：按口播文本、搜索词、候选标题/tags/元数据亲自打分',
    '- [ ] finalize-review round N：代码只转写 LLM 分数，不做视觉审核、不重新打分',
    '- [ ] review-check round N：冻结本轮审核',
    '- [ ] threshold check：逐点统计是否已有 3 个 55+ 且 1 个 75+',
    '- [ ] next round decision：达标点停止；未达标点进入下一轮，最多第 6 轮',
    '- [ ] download：全部达标或第 6 轮结束后下载可用候选',
    '',
    '## 装配',
    '- [ ] prepare_assembly：生成装配数据',
    '- [ ] validate_assembly：校验装配目录',
    '- [ ] serve_review：启动审片替换服务',
  ];
  fs.writeFileSync(checklistPath, `${lines.join('\n')}\n`, 'utf8');
}

function validateAudioPath(audioPath) {
  const resolved = path.resolve(audioPath);
  if (!fs.existsSync(resolved)) throw new Error(`Audio path does not exist: ${resolved}`);
  const stat = fs.statSync(resolved);
  if (!stat.isFile()) throw new Error(`--audio must be one pure audio file, not a directory: ${resolved}`);
  const ext = path.extname(resolved).toLowerCase();
  const audioExts = new Set(['.wav', '.wave', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus']);
  if (!audioExts.has(ext)) throw new Error(`--audio only accepts pure audio files: ${resolved}`);
  return resolved;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  if (args.audio && args.bundle) throw new Error(`Use only one input: --audio or --bundle\n${usage()}`);
  if (!args.audio && !args.bundle) throw new Error(`Missing input\n${usage()}`);
  if (!['auto', 'flash', 'v3-standard'].includes(args.engine)) throw new Error('--engine must be auto, flash, or v3-standard');

  const skillDir = path.resolve(__dirname, '..');
  const inputPath = args.audio ? validateAudioPath(args.audio) : path.resolve(args.bundle);
  const title = inferTitle(inputPath, args.title);
  const workRoot = path.resolve(args.workDir || path.join(process.cwd(), 'koubo-video-output'));
  const projectRoot = path.join(workRoot, title);
  const outputsRoot = path.join(projectRoot, 'outputs');
  const materialProject = path.join(outputsRoot, title);
  const assemblyDir = path.join(projectRoot, `${title}_assembly`);
  const bundleExtractDir = path.join(projectRoot, 'bundle');
  const transcribeBase = path.join(projectRoot, 'audio_transcribe');
  fs.mkdirSync(projectRoot, { recursive: true });
  fs.mkdirSync(outputsRoot, { recursive: true });
  const checklistPath = path.join(projectRoot, '流程单.md');
  writeWorkflowChecklist(checklistPath, title, args.audio ? 'audio' : 'bundle');

  let bundleDir = bundleExtractDir;
  let bundleInfo = null;
  const checkBundleScript = path.join(__dirname, 'check_bundle.js');
  if (args.bundle) {
    const checkArgs = ['--bundle', inputPath, '--extract-to', bundleExtractDir];
    if (args.checkKeys) checkArgs.push('--check-keys');
    bundleInfo = runNode(checkBundleScript, checkArgs);
    bundleDir = bundleInfo.bundle;
  }

  const python = findPythonCommand();
  const seedPath = path.join(materialProject, '分镜规划输入.json');
  const planPath = path.join(materialProject, '素材分段计划.json');
  const transcribeScript = path.join(__dirname, 'transcribe', 'run_transcribe.ps1');
  const transcribeDoctor = path.join(__dirname, 'transcribe', 'doctor.js');
  const makeAudioBundle = path.join(__dirname, 'make_audio_bundle.js');

  const commands = {
    make_seed: `node ${quote(path.join(__dirname, 'plan_seed.js'))} --bundle ${quote(bundleDir)} --title ${quote(title)} --out ${quote(materialProject)}`,
    validate_plan: `${quote(python)} ${quote(path.join(__dirname, 'download_materials.py'))} validate --plan-path ${quote(planPath)} --output-root ${quote(outputsRoot)}`,
    status: `${quote(python)} ${quote(path.join(__dirname, 'download_materials.py'))} status --plan-path ${quote(planPath)} --output-root ${quote(outputsRoot)}`,
    prepare_assembly: `node ${quote(path.join(__dirname, 'prepare.js'))} --bundle ${quote(bundleDir)} --materials ${quote(materialProject)} --out ${quote(assemblyDir)}`,
    validate_assembly: `node ${quote(path.join(__dirname, 'validate.js'))} ${quote(assemblyDir)}`,
    serve_review: process.platform === 'win32'
      ? `powershell -NoProfile -ExecutionPolicy Bypass -File ${quote(path.join(__dirname, 'serve_review.ps1'))} -ProjectDir ${quote(assemblyDir)}`
      : `node ${quote(path.join(__dirname, 'server.js'))} ${quote(assemblyDir)} 0`,
  };

  const nextSteps = [];
  if (args.audio) {
    commands.transcribe_doctor = `node ${quote(transcribeDoctor)} --deps-only --json`;
    commands.transcribe_check = `powershell -NoProfile -ExecutionPolicy Bypass -File ${quote(transcribeScript)} -MediaPath ${quote(inputPath)} -BaseDir ${quote(transcribeBase)} -Engine ${args.engine} -CheckOnly`;
    commands.transcribe = `powershell -NoProfile -ExecutionPolicy Bypass -File ${quote(transcribeScript)} -MediaPath ${quote(inputPath)} -BaseDir ${quote(transcribeBase)} -Engine ${args.engine}`;
    commands.make_audio_bundle = `node ${quote(makeAudioBundle)} --transcribe-dir ${quote(path.join(transcribeBase, '1_转录'))} --title ${quote(title)} --out ${quote(bundleDir)}`;
    commands.check_bundle = `node ${quote(checkBundleScript)} --bundle ${quote(bundleDir)}${args.checkKeys ? ' --check-keys' : ''}`;
    nextSteps.push('Run transcribe_doctor and transcribe_check first.');
    nextSteps.push('Run transcribe to upload the audio to Volcengine and create word timestamps.');
    nextSteps.push('Run make_audio_bundle, then check_bundle.');
  }
  nextSteps.push('Run make_seed.');
  nextSteps.push('Use 分镜规划输入.json to write 素材分段计划.json and 总清单.md, then wait for user confirmation.');
  nextSteps.push('Create/update 流程单.md. Run validate_plan and status; follow only status.next_command until download is complete.');
  nextSteps.push('Run prepare_assembly, resolve 对齐任务.json if needed, then validate_assembly and serve_review.');

  console.log(JSON.stringify({
    ok: true,
    input_mode: args.audio ? 'audio' : 'bundle',
    title,
    skill_dir: skillDir,
    project_root: projectRoot,
    audio_path: args.audio ? inputPath : undefined,
    transcribe_base: args.audio ? transcribeBase : undefined,
    bundle_dir: bundleDir,
    checklist_path: checklistPath,
    files: bundleInfo ? bundleInfo.files : undefined,
    outputs_root: outputsRoot,
    material_project: materialProject,
    assembly_dir: assemblyDir,
    seed_path: seedPath,
    plan_path: planPath,
    python,
    next_steps: nextSteps,
    commands,
  }, null, 2));
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
