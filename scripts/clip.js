#!/usr/bin/env node
// Runs the Python Clip Studio (clipper/) with its own virtual environment.
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const venvPython = process.platform === 'win32'
  ? path.join(root, '.venv-clipper', 'Scripts', 'python.exe')
  : path.join(root, '.venv-clipper', 'bin', 'python');
const python = process.env.CLIPPER_PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python');

const args = process.argv.slice(2);
// `npm run clip:dashboard` passes "dashboard" to open the local web UI instead of the CLI.
const moduleArgs = args[0] === 'dashboard' ? ['-m', 'clipper.dashboard'] : ['-m', 'clipper', ...args];

const child = spawn(python, moduleArgs, {
  cwd: root,
  stdio: 'inherit',
  env: { ...process.env, PYTHONIOENCODING: 'utf-8', HF_HUB_DISABLE_SYMLINKS_WARNING: '1' }
});
child.on('error', error => {
  console.error(`Could not start Python (${python}): ${error.message}`);
  console.error('Set it up with: python -m venv .venv-clipper && .venv-clipper/Scripts/pip install -r clipper/requirements.txt');
  process.exit(1);
});
child.on('close', code => process.exit(code ?? 1));
