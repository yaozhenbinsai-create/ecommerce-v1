import { readdir, readFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const staticRoot = join(projectRoot, 'studio', 'static');
let checked = 0;
let failed = 0;

async function collect(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await collect(path)));
    else if (/\.(?:m?js|html)$/.test(entry.name)) files.push(path);
  }
  return files.sort();
}

function checkModule(source, label) {
  // Parse stdin explicitly as ESM, independent of filename or Node's detection.
  const result = spawnSync(process.execPath, ['--input-type=module', '--check'], {
    input: source,
    encoding: 'utf8',
    windowsHide: true,
    maxBuffer: 1024 * 1024,
  });
  checked += 1;
  if (result.error || result.status !== 0) {
    failed += 1;
    process.stderr.write(`FAIL ${label}\n`);
    process.stderr.write(
      result.error ? `${result.error.message}\n` : result.stderr.replaceAll('[stdin]', label),
    );
  }
}

for (const path of await collect(staticRoot)) {
  const source = await readFile(path, 'utf8');
  const label = relative(projectRoot, path).replaceAll('\\', '/');
  if (/\.m?js$/.test(path)) {
    checkModule(source, label);
    continue;
  }
  let index = 0;
  for (const match of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    if (/\btype\s*=\s*['"]module['"]/i.test(match[1]) && !/\bsrc\s*=/i.test(match[1])) {
      index += 1;
      checkModule(match[2], `${label} (inline module ${index})`);
    }
  }
}

if (failed) {
  process.stderr.write(`${failed} of ${checked} frontend modules failed ESM syntax validation.\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(`PASS: ${checked} frontend modules parsed explicitly as ESM.\n`);
}
