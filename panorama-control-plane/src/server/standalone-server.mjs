import { createServer } from 'node:http';
import { readFile, realpath, stat } from 'node:fs/promises';
import { extname, join, relative, resolve } from 'node:path';

import { analyzeProject, readSourceEvidence } from '../standalone/source-analysis.mjs';
import { WorkspaceStore } from '../standalone/design-workspace.mjs';
import { analyzeConversation, modelConfiguration } from '../standalone/conversation.mjs';
import { authorizeRequest, createCapability, isLoopback, secureHeaders } from './security.mjs';
import { ProcessEvidenceService, compatibilityPreview, importDocument } from '../standalone/process-evidence.mjs';
import { parseProcessJson } from '../process/json.mjs';
import { keys } from '../development/io.mjs';
import packageInfo from '../../package.json' with { type: 'json' };

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

function send(response, status, body, contentType = 'application/json; charset=utf-8') {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(status, secureHeaders(contentType));
  response.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

async function readJson(request, strict = false) {
  if (!String(request.headers['content-type'] || '').startsWith('application/json')) throw Object.assign(new Error('CONTENT_TYPE_REQUIRED'), { statusCode: 415 });
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1024 * 1024) throw Object.assign(new Error('REQUEST_TOO_LARGE'), { statusCode: 413 });
    chunks.push(chunk);
  }
  let body;
  try { body = strict ? parseProcessJson(Buffer.concat(chunks), { maxBytes: 1048576, maxDepth: 24 }) : JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (error) { if (strict) throw error; throw new Error('JSON_INVALID'); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('BODY_INVALID');
  return body;
}

async function staticFile(root, pathname) {
  const canonicalRoot = await realpath(root);
  const decoded = decodeURIComponent(pathname);
  const logical = decoded === '/' ? 'index.html' : decoded.replace(/^\/+/, '');
  if (logical.split(/[\\/]/).some((part) => part === '..' || part.includes(':'))) throw new Error('STATIC_PATH_INVALID');
  const candidate = await realpath(resolve(canonicalRoot, logical));
  const offset = relative(canonicalRoot, candidate);
  if (offset.startsWith('..') || offset.includes(':') || !(await stat(candidate)).isFile()) throw new Error('STATIC_PATH_INVALID');
  return candidate;
}

export async function createStandaloneServer({ projectRoot, dataRoot, staticRoot = join(process.cwd(), 'dist', 'workbench'), port = 0, analyzer = analyzeProject, modelConfig = modelConfiguration(), fetchImpl = fetch } = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('PORT_INVALID');
  const store = new WorkspaceStore({ projectRoot, dataRoot, analyzeProject: analyzer });
  await store.init();
  const processEvidence = new ProcessEvidenceService(store);
  const capability = createCapability();
  let origin;
  let host;
  const service = { name: 'Panorama · 项目理解与设计', version: packageInfo.version, mode: 'standalone' };
  const server = createServer(async (request, response) => {
    try {
      if (!isLoopback(request.socket.remoteAddress) || request.headers.host !== host) return send(response, 403, { error: 'HOST_MISMATCH' });
      const url = new URL(request.url || '/', origin);
      if (url.pathname.startsWith('/api/')) {
        authorizeRequest(request, { expectedHost: host, expectedOrigin: origin, capability, requireOrigin: request.method !== 'GET' });
        if (url.pathname.startsWith('/api/standalone/process')) {
          const path = url.pathname.slice('/api/standalone/'.length);
          if (request.method === 'GET' && path === 'process') return send(response, 200, await processEvidence.catalog(url.searchParams.get('nodeId'), url.searchParams.get('candidateId')));
          if (request.method === 'GET' && path === 'process/work') return send(response, 200, await processEvidence.detail(url.searchParams.get('id')));
          if (request.method === 'GET' && path === 'process/matrix') return send(response, 200, await processEvidence.matrix(url.searchParams.get('id')));
          if (request.method === 'GET' && path === 'process/receipt') return send(response, 200, await processEvidence.receipt(url.searchParams.get('id')));
          if (request.method === 'GET' && path === 'process/assessment') return send(response, 200, await processEvidence.assessment(url.searchParams.get('id')));
          if (request.method === 'POST' && ['process/preview', 'process/assess', 'process/import', 'process/handoff', 'process/compatibility'].includes(path)) {
            const body = await readJson(request, true);
            if (path === 'process/preview' || path === 'process/assess') return send(response, 200, await processEvidence.evaluate(body, path === 'process/assess'));
            if (path === 'process/import') return send(response, 200, await processEvidence.import(body));
            if (path === 'process/handoff') return send(response, 200, await processEvidence.handoff(body));
            keys(body, ['document', 'documentJson']); return send(response, 200, compatibilityPreview(importDocument(body, 'document')));
          }
          return send(response, 404, { error: 'API_NOT_FOUND' });
        }
        if (request.method === 'GET' && url.pathname === '/api/standalone/bootstrap') return send(response, 200, {
          service, workspace: store.snapshot(), model: { available: modelConfig.available, label: modelConfig.label },
        });
        if (request.method === 'GET' && url.pathname === '/api/standalone/source') {
          return send(response, 200, await readSourceEvidence(store.snapshot().current, { path: url.searchParams.get('path'), line: Number(url.searchParams.get('line') || 1), limit: 80 }));
        }
        if (request.method === 'POST' && url.pathname === '/api/standalone/command') {
          const body = await readJson(request);
          // Conversation records can only be produced by the analysis route.
          if (body.type === 'record-conversation') throw new Error('COMMAND_INTERNAL_ONLY');
          return send(response, 200, await store.command(body.type, body.input, body.expectedRevision));
        }
        if (request.method === 'POST' && url.pathname === '/api/standalone/conversation') {
          const body = await readJson(request);
          const result = await analyzeConversation(store.snapshot(), body, { config: modelConfig, fetchImpl });
          const saved = await store.command('record-conversation', {
            sessionId: body.sessionId, message: body.message, response: result,
            baseSnapshotId: result.baseSnapshotId, candidateId: result.candidateId, candidateVersion: result.candidateVersion,
          }, body.expectedRevision);
          return send(response, 200, { ...result, workspace: saved.workspace, revision: saved.workspace.revision });
        }
        return send(response, 404, { error: 'API_NOT_FOUND' });
      }
      if (request.method !== 'GET' && request.method !== 'HEAD') return send(response, 405, { error: 'METHOD_NOT_ALLOWED' });
      let path;
      try { path = await staticFile(staticRoot, url.pathname); }
      catch { return send(response, 404, '页面或构建文件不存在，请运行 pnpm build。', 'text/plain; charset=utf-8'); }
      const data = await readFile(path);
      send(response, 200, request.method === 'HEAD' ? '' : data, MIME[extname(path)] || 'application/octet-stream');
    } catch (error) {
      const code = error.code || error.message || 'REQUEST_FAILED';
      const conflict = /REVISION|STALE|VERSION_CONFLICT|BASELINE_MISMATCH|CONFIGURATION_(RECOVERY_REQUIRED|CHANGED_RETRY)/.test(code);
      send(response, error.statusCode || (conflict ? 409 : 400), { error: code });
    }
  });
  server.requestTimeout = 120000;
  try {
    await new Promise((done, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', done); });
  } catch (error) { await store.close(); throw error; }
  host = `127.0.0.1:${server.address().port}`;
  origin = `http://${host}`;
  let closing;
  return {
    server, store, capability, origin, launchUrl: `${origin}/#cap=${encodeURIComponent(capability)}`,
    close() {
      if (!closing) closing = new Promise((done, reject) => {
        server.close((error) => error ? reject(error) : done());
        server.closeIdleConnections();
      }).finally(() => store.close());
      return closing;
    },
  };
}
