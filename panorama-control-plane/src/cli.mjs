#!/usr/bin/env node
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createStandaloneServer } from './server/standalone-server.mjs';
import { locateProject } from './local-project.mjs';

const args = process.argv.slice(2);
const value = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
};
const command = args[0] ?? 'start';
if (command !== 'start') {
  process.stderr.write('Usage: panorama start [--project PATH] [--data PATH] [--port NUMBER]\n');
  process.exitCode = 2;
} else {
  const { projectRoot, dataRoot } = await locateProject({ project: value('--project', process.cwd()), data: value('--data') });
  const port = Number.parseInt(value('--port', '0'), 10);
  const here = dirname(fileURLToPath(import.meta.url));
  const staticRoot = resolve(here, '..', 'dist', 'workbench');
  const local = await createStandaloneServer({ projectRoot, dataRoot, staticRoot, port: Number.isInteger(port) ? port : 0 });
  process.stdout.write(`Panorama 独立工作台已启动\n${local.launchUrl}\n`);
  const stop = async () => {
    await local.close();
    process.exit(0);
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}
