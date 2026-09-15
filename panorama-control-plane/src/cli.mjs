#!/usr/bin/env node
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isIP } from 'node:net';

import { createStandaloneServer } from './server/standalone-server.mjs';
import { locateProject } from './local-project.mjs';

const args = process.argv.slice(2);
const usage = 'Usage: panorama workbench start [--project PATH] [--data PATH] [--host IP] [--port NUMBER]\nDefault: --host 0.0.0.0 --port 0 (automatic port)\n';
try {
  if (args.includes('--help') || args[0] === 'help') {
    process.stdout.write(usage);
  } else {
    if (args.length && args.shift() !== 'start') throw new Error('COMMAND_INVALID');
    if (args[0] === '--') args.shift();
    const options = new Map();
    for (let index = 0; index < args.length; index += 2) {
      const name = args[index], value = args[index + 1];
      if (!['--project', '--data', '--host', '--port'].includes(name) || options.has(name) || !value || value.startsWith('--')) throw new Error('OPTION_INVALID');
      options.set(name, value);
    }
    const host = options.get('--host') ?? '0.0.0.0';
    const portText = options.get('--port') ?? '0';
    if (!/^\d+$/.test(portText) || Number(portText) > 65535) throw new Error('PORT_INVALID');
    if (!isIP(host)) throw new Error('LISTEN_HOST_INVALID');
    const { projectRoot, dataRoot } = await locateProject({ project: options.get('--project') ?? process.cwd(), data: options.get('--data') });
    const staticRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'workbench');
    const local = await createStandaloneServer({ projectRoot, dataRoot, staticRoot, host, port: Number(portText) });
    process.stdout.write(`Panorama 独立工作台已启动\n监听地址：${host}:${local.port}\n${local.launchUrls.join('\n')}\n使用远程服务器的 IP 与上述端口访问，并保留本次启动链接中的 #cap= 凭证。\n`);
    const stop = async () => { await local.close(); process.exit(0); };
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
  }
} catch (error) {
  process.stderr.write(`${error.code || error.message}\n${usage}`);
  process.exitCode = 2;
}
