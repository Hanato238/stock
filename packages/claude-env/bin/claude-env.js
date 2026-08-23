#!/usr/bin/env node
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const ASSETS = path.join(__dirname, '..', 'assets');
const HOME = os.homedir();

const YAZI_VERSION = '0.4.2';
const ZJSTATUS_VERSION = '0.19.1';

function sh(script) {
  const result = spawnSync('bash', ['-c', script], { stdio: 'inherit' });
  if (result.status !== 0) {
    console.error(`Failed: ${script}`);
    process.exit(1);
  }
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyAsset(src, dest) {
  ensureDir(path.dirname(dest));
  fs.copyFileSync(path.join(ASSETS, src), dest);
  console.log(`  ${dest}`);
}

function mergeHooks(srcPath, destPath) {
  const src = JSON.parse(fs.readFileSync(srcPath, 'utf8'));
  let dest = {};
  if (fs.existsSync(destPath)) {
    try { dest = JSON.parse(fs.readFileSync(destPath, 'utf8')); } catch {}
  }
  dest.hooks = dest.hooks || {};
  for (const [event, entries] of Object.entries(src.hooks || {})) {
    dest.hooks[event] = dest.hooks[event] || [];
    for (const entry of entries) {
      const exists = dest.hooks[event].some(
        e => e.command === entry.command
      );
      if (!exists) dest.hooks[event].push(entry);
    }
  }
  ensureDir(path.dirname(destPath));
  fs.writeFileSync(destPath, JSON.stringify(dest, null, 2) + '\n');
  console.log(`  ${destPath}`);
}

const command = process.argv[2];

// ─── install: yazi をシステムパスに配置（root 必須）──────────────────────────
if (command === 'install') {
  const isRoot = process.getuid && process.getuid() === 0;
  if (!isRoot) {
    console.error('[claude-env] "install" requires root. Run as root or via sudo.');
    process.exit(1);
  }

  console.log(`[claude-env] Installing yazi v${YAZI_VERSION}...`);
  sh([
    `curl -fsSL "https://github.com/sxyazi/yazi/releases/download/v${YAZI_VERSION}/yazi-x86_64-unknown-linux-musl.zip" -o /tmp/yazi.zip`,
    `unzip -qo /tmp/yazi.zip "yazi-x86_64-unknown-linux-musl/yazi" -d /tmp/yazi_ex`,
    `mv /tmp/yazi_ex/yazi-x86_64-unknown-linux-musl/yazi /usr/local/bin/yazi`,
    `chmod +x /usr/local/bin/yazi`,
    `rm -rf /tmp/yazi.zip /tmp/yazi_ex`,
  ].join(' && '));
  console.log('[claude-env] yazi installed.');

// ─── setup: ユーザーホームへ設定ファイルを配置 ─────────────────────────────
} else if (command === 'setup') {
  const zellijDir     = path.join(HOME, '.config', 'zellij');
  const pluginsDir    = path.join(zellijDir, 'plugins');
  const layoutsDir    = path.join(zellijDir, 'layouts');
  const scriptsDir    = path.join(zellijDir, 'scripts');
  const yaziDir       = path.join(HOME, '.config', 'yazi');
  const claudeDir     = path.join(HOME, '.claude');
  const hooksDir      = path.join(claudeDir, 'hooks');
  const hooksScript   = path.join(hooksDir, 'update-stats.sh');
  const settingsPath  = path.join(claudeDir, 'settings.json');

  console.log(`[claude-env] Downloading zjstatus v${ZJSTATUS_VERSION}...`);
  ensureDir(pluginsDir);
  sh(`curl -fsSL "https://github.com/dj95/zjstatus/releases/download/v${ZJSTATUS_VERSION}/zjstatus.wasm" -o "${path.join(pluginsDir, 'zjstatus.wasm')}"`);

  console.log('[claude-env] Installing config files:');
  copyAsset('zellij-layout.kdl', path.join(layoutsDir, 'claude.kdl'));
  copyAsset('zellij-config.kdl', path.join(zellijDir, 'config.kdl'));
  copyAsset('yazi-theme.toml',   path.join(yaziDir, 'theme.toml'));

  console.log('[claude-env] Installing zjstatus scripts:');
  for (const name of ['claude-model.sh', 'claude-tokens.sh', 'git-branch.sh', 'project-name.sh']) {
    const dest = path.join(scriptsDir, name);
    copyAsset(`scripts/${name}`, dest);
    fs.chmodSync(dest, 0o755);
  }

  console.log('[claude-env] Registering Claude hooks:');
  mergeHooks(path.join(ASSETS, 'claude-settings.json'), settingsPath);

  console.log('[claude-env] Installing hooks script:');
  ensureDir(hooksDir);
  copyAsset('hooks/update-stats.sh', hooksScript);
  fs.chmodSync(hooksScript, 0o755);

  console.log('[claude-env] Setup complete.');

} else {
  console.error('Usage: claude-env install   # root: yazi をシステムにインストール');
  console.error('       claude-env setup     # user: zellij/claude 設定を配置');
  process.exit(1);
}
