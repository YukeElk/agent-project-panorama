import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { lstat, open, readdir, realpath } from 'node:fs/promises';
import { basename, dirname, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { parseDocument } from 'yaml';

export const PARSER_VERSION = 'panorama-source-1.0.0-ts5.9.3';
const OMIT_DIRECTORIES = new Set(['.git', '.hg', '.svn', 'node_modules', 'vendor', 'dist', 'build', 'coverage', '.next', '.nuxt', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.panorama-work', 'evals', '.ssh', '.gnupg', 'secrets', 'credentials', 'target', 'bin', 'obj']);
const CODE_LANGUAGES = new Map([['.js', 'javascript'], ['.jsx', 'javascript'], ['.mjs', 'javascript'], ['.cjs', 'javascript'], ['.ts', 'typescript'], ['.tsx', 'typescript'], ['.mts', 'typescript'], ['.cts', 'typescript'], ['.py', 'python']]);
const OTHER_SOURCE = new Set(['.java', '.go', '.rs', '.rb', '.php', '.cs', '.kt', '.kts', '.swift', '.scala', '.c', '.cc', '.cpp', '.h', '.hpp', '.vue', '.svelte', '.dart', '.ex', '.exs', '.fs', '.lua', '.sql', '.proto', '.graphql', '.gql']);
const COMPOSE_NAMES = new Set(['compose.yml', 'compose.yaml', 'docker-compose.yml', 'docker-compose.yaml']);
const DEFAULT_LIMITS = { maxFiles: 5000, maxFileBytes: 1024 * 1024, maxTotalBytes: 64 * 1024 * 1024, maxDepth: 32, maxEntries: 30000 };
const posix = (value) => value.split(sep).join('/');
const digest = (value) => createHash('sha256').update(value).digest('hex');
const stableId = (kind, identity) => `${kind}:${digest(identity).slice(0, 24)}`;
const evidence = (path, line, kind, detail) => ({ path, line, kind, detail });
const gap = (code, path, message) => ({ code, path, message });
function within(root, path) { const offset = relative(root, path); return offset === '' || (!offset.startsWith(`..${sep}`) && offset !== '..' && !isAbsolute(offset)); }
function secretPath(path) {
  const name = basename(path).toLowerCase();
  return name === '.env' || name.startsWith('.env.') || /\.(pem|key|p12|pfx|jks|keystore)$/i.test(name) || /^(credentials|service-account)\.(json|ya?ml)$/.test(name) || /^id_(rsa|ed25519)/.test(name);
}
function languageFor(path) {
  const name = basename(path).toLowerCase();
  if (COMPOSE_NAMES.has(name)) return 'compose';
  if (name === 'package.json') return 'package-json';
  if (/^(tsconfig|jsconfig)(\.[^.]+)?\.json$/.test(name)) return 'tsconfig';
  if (name === 'pyproject.toml') return 'python-manifest';
  if (CODE_LANGUAGES.has(extname(name))) return CODE_LANGUAGES.get(extname(name));
  if (OTHER_SOURCE.has(extname(name))) return `unsupported:${extname(name).slice(1)}`;
  if (/\.(ya?ml|tf)$/.test(name) || name === 'dockerfile' || name.startsWith('dockerfile.')) return 'unsupported:deployment';
  return null;
}

async function canonicalRoot(projectRoot) {
  const logical = resolve(projectRoot);
  if ((await lstat(logical)).isSymbolicLink()) throw new Error('SOURCE_ROOT_LINK');
  const canonical = await realpath(logical);
  if (!(await lstat(canonical)).isDirectory()) throw new Error('SOURCE_ROOT_NOT_DIRECTORY');
  return canonical;
}

async function safeFile(root, path, maxBytes) {
  if (typeof path !== 'string' || isAbsolute(path) || path.includes('\\') || path.split('/').some((part) => part === '..' || part === '' || part.includes(':'))) throw new Error('SOURCE_PATH_INVALID');
  let candidate = root;
  for (const part of path.split('/')) {
    candidate = join(candidate, part);
    if ((await lstat(candidate)).isSymbolicLink()) throw new Error('SOURCE_LINK_REJECTED');
  }
  const canonical = await realpath(candidate);
  if (!within(root, canonical)) throw new Error('SOURCE_PATH_ESCAPE');
  const before = await lstat(candidate);
  if (!before.isFile()) throw new Error('SOURCE_NOT_FILE');
  if (before.size > maxBytes) throw new Error('SOURCE_FILE_LIMIT');
  const handle = await open(candidate, 'r');
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.size > maxBytes || opened.dev !== before.dev || opened.ino !== before.ino) throw new Error('SOURCE_CHANGED_DURING_SCAN');
    const body = await handle.readFile();
    const after = await handle.stat();
    if (body.length > maxBytes || body.length !== before.size || after.size !== before.size || after.mtimeMs !== opened.mtimeMs) throw new Error('SOURCE_CHANGED_DURING_SCAN');
    if ((await lstat(candidate)).isSymbolicLink() || await realpath(candidate) !== canonical) throw new Error('SOURCE_CHANGED_DURING_SCAN');
    return body;
  } finally { await handle.close(); }
}

async function scan(root, limits) {
  const files = [];
  const gaps = [];
  const inventory = [];
  let entries = 0;
  let bytes = 0;
  let discovered = 0;
  let entryLimited = false;
  async function visit(directory, depth) {
    if (depth > limits.maxDepth) { gaps.push(gap('DEPTH_LIMIT', posix(relative(root, directory)), '目录超过扫描深度预算。')); return; }
    const children = (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name, 'en'));
    for (const child of children) {
      if (++entries > limits.maxEntries) { entryLimited = true; return; }
      const absolute = join(directory, child.name);
      const path = posix(relative(root, absolute));
      if (secretPath(path) || OMIT_DIRECTORIES.has(child.name.toLowerCase())) continue;
      if (child.isSymbolicLink()) { gaps.push(gap('LINK_SKIPPED', path, '未读取链接或联接目标。')); inventory.push(`link:${path}`); continue; }
      if (child.isDirectory()) { await visit(absolute, depth + 1); if (entryLimited) return; continue; }
      const language = languageFor(path);
      if (!child.isFile() || !language) continue;
      const info = await lstat(absolute);
      inventory.push(`${path}:${info.size}:${info.mtimeMs}`);
      discovered += 1;
      if (discovered > limits.maxFiles) { gaps.push(gap('FILE_COUNT_LIMIT', path, '文件数量超过扫描预算。')); continue; }
      if (info.size > limits.maxFileBytes) { gaps.push(gap('FILE_SIZE_LIMIT', path, '文件超过单文件读取预算。')); continue; }
      if (bytes + info.size > limits.maxTotalBytes) { gaps.push(gap('TOTAL_SIZE_LIMIT', path, '源码总量超过读取预算。')); continue; }
      const body = await safeFile(root, path, limits.maxFileBytes);
      bytes += body.length;
      if (body.includes(0)) { gaps.push(gap('BINARY_SKIPPED', path, '文件包含二进制内容。')); continue; }
      let text;
      try { text = new TextDecoder('utf-8', { fatal: true }).decode(body); }
      catch { gaps.push(gap('ENCODING_UNSUPPORTED', path, '文件不是有效 UTF-8，未解析其内容。')); continue; }
      files.push({ path, digest: digest(body), size: body.length, language, text });
    }
  }
  await visit(root, 0);
  if (entryLimited) gaps.push(gap('ENTRY_COUNT_LIMIT', '', '目录条目超过扫描预算，结果不完整。'));
  return { files: files.sort((a, b) => a.path.localeCompare(b.path, 'en')), gaps, inventory: inventory.sort() };
}

function runProcess(command, args, input, timeoutMs = 15000, maxOutputBytes = 16 * 1024 * 1024) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(command, args, { windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let length = 0;
    const timer = setTimeout(() => { child.kill(); reject(new Error('PYTHON_TIMEOUT')); }, timeoutMs);
    child.on('error', (error) => { clearTimeout(timer); reject(error); });
    child.stdout.on('data', (chunk) => {
      length += chunk.length;
      if (length > maxOutputBytes) { child.kill(); clearTimeout(timer); reject(new Error('PYTHON_OUTPUT_LIMIT')); }
      else stdout += chunk.toString('utf8');
    });
    child.stderr.resume();
    child.stdin.on('error', () => {});
    child.on('close', (code) => { clearTimeout(timer); if (code === 0) resolveResult(stdout); else reject(new Error('PYTHON_PARSER_FAILED')); });
    child.stdin.end(input);
  });
}

async function parsePython(files, options) {
  const explicit = options.pythonPath ?? process.env.PANORAMA_PYTHON;
  const candidates = explicit ? [[explicit, []]] : (process.platform === 'win32' ? [['py', ['-3']], ['python3', []], ['python', []]] : [['python3', []], ['python', []]]);
  for (const [command, prefix] of candidates) {
    try {
      const version = await runProcess(command, [...prefix, '-I', '-B', '-c', 'import sys; print(sys.version_info.major, sys.version_info.minor)'], '', 3000, 1000);
      const [major, minor] = version.trim().split(' ').map(Number);
      if (major !== 3 || minor < 9) continue;
      const result = await runProcess(command, [...prefix, '-I', '-B', fileURLToPath(new URL('./python-ast.py', import.meta.url))], JSON.stringify({ files: files.map(({ path, text }) => ({ path, text })) }), options.pythonTimeoutMs ?? 15000);
      return { available: true, files: JSON.parse(result).files };
    } catch { /* Try another explicitly enumerated interpreter; never execute project modules. */ }
  }
  return { available: false, files: [] };
}

function createBuilder(projectId, files) {
  const nodes = [];
  const edges = [];
  const edgeIds = new Map();
  const byId = new Map();
  const fileMap = new Map();
  const identity = (kind, key) => stableId(kind, `${projectId}:${key}`);
  function node(kind, key, label, properties = {}) {
    const id = identity(kind, key);
    if (byId.has(id)) return byId.get(id);
    if (nodes.length >= 50000) throw new Error('MODEL_NODE_LIMIT');
    const value = { id, kind, label, parentId: null, sourcePath: null, line: null, description: '', attributes: {}, evidence: [], ...properties };
    nodes.push(value); byId.set(id, value); return value;
  }
  function edge(from, to, kind, label, pins, attributes = {}) {
    const id = identity('edge', `${from}:${to}:${kind}:${label}`);
    const previous = edgeIds.get(id);
    if (previous) { for (const pin of pins) if (!previous.evidence.some((item) => JSON.stringify(item) === JSON.stringify(pin))) previous.evidence.push(pin); return previous; }
    if (edges.length >= 200000) throw new Error('MODEL_EDGE_LIMIT');
    const value = { id, from, to, kind, label, evidence: pins, confidence: 'high', resolution: 'resolved', attributes: { runtimeObserved: false, ...attributes } };
    edges.push(value); edgeIds.set(id, value); return value;
  }
  function folder(path, pin) {
    if (path === '.' || path === '') return null;
    const parent = folder(posix(dirname(path)), pin);
    const value = node('module', path, basename(path), { parentId: parent?.id ?? null, sourcePath: path, description: `源码目录分组：${path}；不代表已确认的业务职责。`, attributes: { grouping: 'source-directory', observation: 'inferred' }, evidence: [pin] });
    if (parent) edge(parent.id, value.id, 'contains', '包含', [pin]);
    return value;
  }
  for (const file of files) {
    const pin = evidence(file.path, 1, 'source-file', '本次快照读取的源码或配置文件。');
    const parent = folder(posix(dirname(file.path)), pin);
    const value = node('file', file.path, basename(file.path), { parentId: parent?.id ?? null, sourcePath: file.path, line: 1, attributes: { language: file.language, observation: 'observed' }, evidence: [pin] });
    fileMap.set(file.path, value);
    if (parent) edge(parent.id, value.id, 'contains', '包含', [pin]);
  }
  return { nodes, edges, byId, fileMap, node, edge, identity };
}

function jsResolver(files, builder, gaps) {
  const paths = new Set(files.map((file) => file.path));
  const configs = [];
  for (const file of files.filter((item) => item.language === 'tsconfig')) {
    const parsed = ts.parseConfigFileTextToJson(file.path, file.text);
    if (parsed.error) { gaps.push(gap('CONFIG_PARSE_ERROR', file.path, 'TypeScript 配置无法解析。')); continue; }
    const config = parsed.config;
    if (!config || typeof config !== 'object' || Array.isArray(config)) { gaps.push(gap('CONFIG_PARSE_ERROR', file.path, 'TypeScript 配置必须是 JSON 对象。')); continue; }
    if (config.extends) gaps.push(gap('CONFIG_EXTENDS_UNRESOLVED', file.path, '未加载继承配置；只使用当前文件显式声明的路径映射。'));
    configs.push({ root: posix(dirname(file.path)), options: config.compilerOptions ?? {} });
  }
  const findPath = (value) => {
    const candidates = [value, ...['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts', '/index.ts', '/index.tsx', '/index.js', '/index.jsx'].map((suffix) => `${value}${suffix}`)];
    if (/\.(m?js|cjs)$/.test(value)) candidates.push(value.replace(/\.mjs$/, '.mts').replace(/\.cjs$/, '.cts').replace(/\.js$/, '.ts'), value.replace(/\.js$/, '.tsx'));
    return candidates.find((candidate) => paths.has(candidate)) ?? null;
  };
  return (source, specifier, line, kind, typeOnly = false) => {
    let targetPath = null;
    let alias = false;
    const sourceRoot = dirname(source.path);
    if (specifier.startsWith('.')) targetPath = findPath(posix(join(sourceRoot, specifier)));
    else {
      const config = configs.filter((item) => item.root === '.' || source.path.startsWith(`${item.root}/`)).sort((a, b) => b.root.length - a.root.length)[0];
      for (const [pattern, targets] of Object.entries(config?.options.paths ?? {})) {
        const [before, after = ''] = pattern.split('*');
        if ((pattern.includes('*') && specifier.startsWith(before) && specifier.endsWith(after)) || pattern === specifier) {
          alias = true;
          const middle = pattern.includes('*') ? specifier.slice(before.length, after.length ? -after.length : undefined) : '';
          for (const target of Array.isArray(targets) ? targets : []) {
            if (typeof target !== 'string') continue;
            const candidate = posix(join(config.root, String(config.options.baseUrl ?? '.'), target.replace('*', middle)));
            if (!candidate.startsWith('../')) targetPath = findPath(candidate);
            if (targetPath) break;
          }
        }
      }
    }
    const pin = evidence(source.path, line, 'static-import', `静态声明 ${specifier}，不证明运行调用。`);
    let target = targetPath ? builder.fileMap.get(targetPath) : null;
    if (!target) {
      const unresolved = specifier.startsWith('.') || alias || specifier.startsWith('@/') || specifier.startsWith('~/') || specifier.startsWith('#') || specifier.startsWith('/');
      target = builder.node('external', `${unresolved ? source.path : ''}:${specifier}`, specifier, { description: unresolved ? '未解析的本地或别名依赖。' : '外部依赖声明，未解析安装和运行状态。', attributes: { observation: 'declared', unresolved }, evidence: [pin] });
      if (unresolved) gaps.push(gap('IMPORT_UNRESOLVED', source.path, `无法解析 ${specifier}。`));
    }
    const relation = builder.edge(builder.fileMap.get(source.path).id, target.id, kind, specifier, [pin], { specifier, typeOnly, dynamic: kind === 'dynamic-import' });
    if (target.attributes.unresolved) { relation.resolution = 'unresolved'; relation.confidence = 'low'; }
  };
}

function parseJavaScript(file, builder, resolveImport, gaps) {
  const kind = /\.[jt]sx$/.test(file.path) ? ts.ScriptKind.TSX : file.language === 'typescript' ? ts.ScriptKind.TS : ts.ScriptKind.JS;
  const ast = ts.createSourceFile(file.path, file.text, ts.ScriptTarget.Latest, true, kind);
  if (ast.parseDiagnostics.length) gaps.push(gap('JAVASCRIPT_PARSE_ERROR', file.path, '源码包含语法错误，已提取的部分声明不表示完整解析。'));
  const lineOf = (node) => ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1;
  const fileNode = builder.fileMap.get(file.path);
  const expressNames = new Set();
  const routers = new Set();
  for (const statement of ast.statements) {
    if (ts.isImportDeclaration(statement) && statement.moduleSpecifier.text === 'express' && statement.importClause?.name) expressNames.add(statement.importClause.name.text);
    if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations) {
      if (ts.isIdentifier(declaration.name) && declaration.initializer && ts.isCallExpression(declaration.initializer) && ts.isIdentifier(declaration.initializer.expression) && declaration.initializer.expression.text === 'require' && declaration.initializer.arguments[0]?.text === 'express') expressNames.add(declaration.name.text);
    }
  }
  for (const statement of ast.statements) if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations) {
    if (!ts.isIdentifier(declaration.name) || !declaration.initializer || !ts.isCallExpression(declaration.initializer)) continue;
    const expression = declaration.initializer.expression;
    if ((ts.isIdentifier(expression) && expressNames.has(expression.text)) || (ts.isPropertyAccessExpression(expression) && expression.name.text === 'Router' && ts.isIdentifier(expression.expression) && expressNames.has(expression.expression.text))) routers.add(declaration.name.text);
  }
  function visit(node, scope = []) {
    let nextScope = scope;
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      const specifier = node.moduleSpecifier;
      if (specifier && ts.isStringLiteralLike(specifier)) {
        const typeOnly = Boolean(node.isTypeOnly || node.importClause?.isTypeOnly || (node.importClause?.namedBindings && ts.isNamedImports(node.importClause.namedBindings) && !node.importClause.name && node.importClause.namedBindings.elements.length && node.importClause.namedBindings.elements.every((item) => item.isTypeOnly)));
        resolveImport(file, specifier.text, lineOf(node), typeOnly ? 'type-import' : 'imports', typeOnly);
      }
    }
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference) && ts.isStringLiteralLike(node.moduleReference.expression)) resolveImport(file, node.moduleReference.expression.text, lineOf(node), node.isTypeOnly ? 'type-import' : 'imports', Boolean(node.isTypeOnly));
    if (ts.isCallExpression(node)) {
      const dynamic = node.expression.kind === ts.SyntaxKind.ImportKeyword;
      const requireCall = ts.isIdentifier(node.expression) && node.expression.text === 'require';
      if (dynamic || requireCall) {
        if (node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) resolveImport(file, node.arguments[0].text, lineOf(node), dynamic ? 'dynamic-import' : 'imports');
        else gaps.push(gap('DYNAMIC_IMPORT', file.path, `第 ${lineOf(node)} 行的动态依赖目标不是字面量。`));
      }
      if (ts.isPropertyAccessExpression(node.expression) && ts.isIdentifier(node.expression.expression) && routers.has(node.expression.expression.text) && ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'use'].includes(node.expression.name.text)) {
        if (node.arguments[0] && ts.isStringLiteralLike(node.arguments[0])) {
          const label = `${node.expression.name.text.toUpperCase()} ${node.arguments[0].text}`;
          const pin = evidence(file.path, lineOf(node), 'route-declaration', 'Express 路由注册语法；不证明运行时挂载或请求成功。');
          const route = builder.node('route', `${file.path}:${label}`, label, { parentId: fileNode.id, sourcePath: file.path, line: lineOf(node), attributes: { framework: 'express', observation: 'declared', method: node.expression.name.text.toUpperCase(), route: node.arguments[0].text }, evidence: [pin] });
          builder.edge(fileNode.id, route.id, 'serves', '声明路由', [pin]);
        } else gaps.push(gap('DYNAMIC_ROUTE', file.path, '路由路径不是字面量，未展开。'));
      }
    }
    if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) {
      const name = node.name.getText(ast);
      const qualifiedName = [...scope, name].join('.');
      const pin = evidence(file.path, lineOf(node), 'symbol-declaration', '源码中的符号声明。');
      const symbol = builder.node('symbol', `${file.path}:${qualifiedName}`, name, { parentId: fileNode.id, sourcePath: file.path, line: lineOf(node), attributes: { declarationKind: ts.SyntaxKind[node.kind], qualifiedName, observation: 'declared' }, evidence: [pin] });
      builder.edge(fileNode.id, symbol.id, 'contains', '声明', [pin]);
      nextScope = [...scope, name];
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
      const name = [...scope, node.name.text].join('.');
      const pin = evidence(file.path, lineOf(node), 'symbol-declaration', '函数变量声明。');
      const symbol = builder.node('symbol', `${file.path}:${name}`, node.name.text, { parentId: fileNode.id, sourcePath: file.path, line: lineOf(node), attributes: { declarationKind: 'function-variable', qualifiedName: name, observation: 'declared' }, evidence: [pin] });
      builder.edge(fileNode.id, symbol.id, 'contains', '声明', [pin]);
    }
    ts.forEachChild(node, (child) => visit(child, nextScope));
  }
  visit(ast);
}

function addPythonResults(results, files, builder, gaps) {
  const modules = new Map();
  for (const file of files.filter((item) => item.language === 'python')) {
    const full = file.path.replace(/\.py$/, '').replace(/\/__init__$/, '').replaceAll('/', '.');
    for (const key of new Set([full, full.replace(/^src\./, '')])) {
      if (!modules.has(key)) modules.set(key, []);
      modules.get(key).push(file.path);
    }
  }
  for (const result of results) {
    const fileNode = builder.fileMap.get(result.path);
    for (const issue of result.gaps) gaps.push(gap(issue.code, result.path, `${issue.message} (line ${issue.line})`));
    for (const declaration of result.symbols) {
      const pin = evidence(result.path, declaration.line, 'symbol-declaration', 'Python AST 符号声明。');
      const symbol = builder.node('symbol', `${result.path}:${declaration.qualifiedName}`, declaration.name, { parentId: fileNode.id, sourcePath: result.path, line: declaration.line, attributes: { declarationKind: declaration.kind, qualifiedName: declaration.qualifiedName, observation: 'declared' }, evidence: [pin] });
      builder.edge(fileNode.id, symbol.id, 'contains', '声明', [pin]);
    }
    for (const route of result.routes) {
      const label = `${route.method} ${route.route}`;
      const pin = evidence(result.path, route.line, 'route-declaration', 'FastAPI/Flask 字面量装饰器；不证明实际运行挂载。');
      const value = builder.node('route', `${result.path}:${label}`, label, { parentId: fileNode.id, sourcePath: result.path, line: route.line, attributes: { method: route.method, route: route.route, handler: route.handler, observation: 'declared' }, evidence: [pin] });
      builder.edge(fileNode.id, value.id, 'serves', '声明路由', [pin]);
    }
    for (const declaration of result.imports) {
      const packageParts = result.path.replace(/\.py$/, '').split('/');
      packageParts.pop();
      let specifier = declaration.module;
      if (declaration.level > 0) specifier = [...packageParts.slice(0, Math.max(0, packageParts.length - declaration.level + 1)), ...declaration.module.split('.').filter(Boolean)].join('.');
      const candidateNames = [...declaration.names.filter((name) => name !== '*').map((name) => [specifier, name].filter(Boolean).join('.')), specifier];
      let resolved = false;
      for (const candidate of candidateNames) {
        const paths = modules.get(candidate) ?? [];
        if (paths.length !== 1) continue;
        const pin = evidence(result.path, declaration.line, 'static-import', `Python 导入 ${candidate}；不是运行调用。`);
        builder.edge(fileNode.id, builder.fileMap.get(paths[0]).id, declaration.kind, candidate, [pin], { specifier: candidate });
        resolved = true;
        if (candidate === specifier) break;
      }
      if (!resolved) {
        const pin = evidence(result.path, declaration.line, 'static-import', `Python 导入 ${specifier || '.'}。`);
        const external = builder.node('external', `${declaration.level ? result.path : ''}:${specifier || '.'}`, specifier || '.', { attributes: { observation: 'declared', unresolved: declaration.level > 0 }, evidence: [pin] });
        const edge = builder.edge(fileNode.id, external.id, declaration.kind, specifier || '.', [pin]);
        if (declaration.level > 0) { edge.resolution = 'unresolved'; edge.confidence = 'low'; gaps.push(gap('IMPORT_UNRESOLVED', result.path, '相对导入目标无法唯一解析。')); }
      }
    }
  }
}

function parseCompose(file, builder, gaps) {
  const document = parseDocument(file.text, { keepSourceTokens: true, uniqueKeys: true, strict: true });
  if (document.errors.length) { gaps.push(gap('COMPOSE_PARSE_ERROR', file.path, 'Compose YAML 无法解析。')); return; }
  let value;
  try { value = document.toJS({ maxAliasCount: 40 }); } catch { gaps.push(gap('COMPOSE_PARSE_ERROR', file.path, 'Compose YAML 别名展开超过预算。')); return; }
  if (!value || typeof value !== 'object' || !value.services || Array.isArray(value.services) || typeof value.services !== 'object') { gaps.push(gap('COMPOSE_SERVICES_MISSING', file.path, '未取得 services 对象，不能生成部署关系。')); return; }
  const lineFor = (path) => { const node = document.getIn(path, true); return node?.range ? file.text.slice(0, node.range[0]).split('\n').length : 1; };
  const serviceMap = new Map();
  const resources = new Map();
  const isRecord = (input) => input !== null && typeof input === 'object' && !Array.isArray(input);
  const names = (input, field) => {
    if (input === undefined || input === null) return [];
    if (Array.isArray(input) && input.every((item) => typeof item === 'string')) return input;
    if (isRecord(input)) return Object.keys(input);
    gaps.push(gap('COMPOSE_FIELD_INVALID', file.path, `${field} 必须是名称列表或映射，未生成关系。`)); return [];
  };
  const declaredString = (input) => typeof input === 'string' ? input.slice(0, 500) : null;
  function resource(kind, name, line, declared) {
    const key = `${kind}:${name}`;
    if (resources.has(key)) return resources.get(key);
    const pin = evidence(file.path, line, 'compose-declaration', `Compose ${kind} 名称声明。`);
    const node = builder.node('resource', `${file.path}:${key}`, name, { sourcePath: file.path, line, description: `配置声明的 ${kind}，未核对运行环境。`, attributes: { resourceKind: kind, observation: 'declared', declared }, evidence: [pin] });
    resources.set(key, node); return node;
  }
  for (const kind of ['volumes', 'networks']) {
    if (value[kind] !== undefined && !isRecord(value[kind])) { gaps.push(gap('COMPOSE_FIELD_INVALID', file.path, `${kind} 顶层声明必须是映射。`)); continue; }
    for (const name of Object.keys(value[kind] ?? {})) resource(kind, name, lineFor([kind, name]), true);
  }
  for (const [name, config] of Object.entries(value.services)) {
    if (!config || typeof config !== 'object' || Array.isArray(config)) { gaps.push(gap('COMPOSE_SERVICE_INVALID', file.path, `服务 ${name} 不是配置对象。`)); continue; }
    const line = lineFor(['services', name]);
    const pin = evidence(file.path, line, 'compose-declaration', `Compose 服务 ${name}；未执行或部署。`);
    const ports = [];
    if (config.ports !== undefined && !Array.isArray(config.ports)) gaps.push(gap('COMPOSE_FIELD_INVALID', file.path, `服务 ${name} 的 ports 不是列表。`));
    for (const port of Array.isArray(config.ports) ? config.ports : []) {
      if (typeof port === 'string' || typeof port === 'number') ports.push(String(port));
      else if (port && typeof port === 'object' && ['number', 'string'].includes(typeof port.target)) ports.push(`${port.published ?? ''}:${port.target}/${port.protocol ?? 'tcp'}`);
    }
    const envKeys = names(config.environment, 'environment').map((item) => item.split('=', 1)[0]);
    const replicas = config.deploy?.replicas;
    if (replicas !== undefined && (!Number.isInteger(replicas) || replicas < 0)) gaps.push(gap('COMPOSE_FIELD_UNRESOLVED', file.path, `服务 ${name} 的副本数不是非负整数字面量，未推断其值。`));
    const node = builder.node('service', `${file.path}:${name}`, name, { sourcePath: file.path, line, description: 'Compose 声明的服务；不代表已部署或正在运行。', attributes: { observation: 'declared', deployment: { environment: 'compose', ...(replicas === undefined ? { replicas: 1 } : Number.isInteger(replicas) && replicas >= 0 ? { replicas } : {}), ports, ...(declaredString(config.image) ? { image: declaredString(config.image) } : {}) }, build: typeof config.build === 'string' ? { context: config.build } : config.build && typeof config.build === 'object' ? { context: declaredString(config.build.context), dockerfile: declaredString(config.build.dockerfile) } : null, environmentKeys: [...new Set(envKeys)].sort() }, evidence: [pin] });
    serviceMap.set(name, node);
    builder.edge(builder.fileMap.get(file.path).id, node.id, 'declares', '配置声明', [pin]);
    const supported = new Set(['image', 'build', 'ports', 'depends_on', 'volumes', 'networks', 'environment', 'deploy', 'env_file']);
    const unknown = Object.keys(config).filter((key) => !supported.has(key));
    if (unknown.length) gaps.push(gap('COMPOSE_PARTIAL', file.path, `服务 ${name} 存在未分析配置键：${unknown.join(', ')}。`));
    if (isRecord(config.deploy) && Object.keys(config.deploy).some((key) => key !== 'replicas')) gaps.push(gap('COMPOSE_PARTIAL', file.path, `服务 ${name} 的 deploy 仅分析 replicas，其他配置未解释。`));
    if (config.env_file) gaps.push(gap('ENV_FILE_NOT_READ', file.path, `服务 ${name} 引用的环境文件未读取。`));
  }
  for (const [name, config] of Object.entries(value.services)) {
    const from = serviceMap.get(name);
    if (!from) continue;
    const dependencies = names(config.depends_on, 'depends_on');
    for (const dependency of dependencies) {
      const to = serviceMap.get(dependency);
      if (!to) { gaps.push(gap('COMPOSE_DEPENDENCY_UNRESOLVED', file.path, `服务 ${name} 引用不存在的 depends_on 服务。`)); continue; }
      builder.edge(from.id, to.id, 'depends-on', '声明启动依赖', [evidence(file.path, lineFor(['services', name, 'depends_on']), 'compose-declaration', 'depends_on 表示配置约束，不证明调用或通信。')]);
    }
    const networkNames = names(config.networks, 'networks');
    for (const network of networkNames) {
      if (typeof network !== 'string') continue;
      const line = lineFor(['services', name, 'networks']);
      const to = resource('networks', network, line, isRecord(value.networks) && Object.hasOwn(value.networks, network));
      builder.edge(from.id, to.id, 'uses', '声明网络', [evidence(file.path, line, 'compose-declaration', '声明的网络成员关系。')]);
    }
    if (config.volumes !== undefined && !Array.isArray(config.volumes)) gaps.push(gap('COMPOSE_FIELD_INVALID', file.path, `服务 ${name} 的 volumes 不是列表。`));
    for (const volume of Array.isArray(config.volumes) ? config.volumes : []) {
      let source; let type = 'volumes';
      if (typeof volume === 'string') { const parts = volume.split(':'); if (parts.length < 2) continue; source = parts[0]; if (/^[./~]/.test(source) || /^[A-Za-z]$/.test(source)) type = 'bind-mount'; }
      else if (volume && typeof volume === 'object') { source = volume.source; if (volume.type === 'bind') type = 'bind-mount'; }
      if (typeof source !== 'string') continue;
      const line = lineFor(['services', name, 'volumes']);
      const to = resource(type, source, line, type === 'bind-mount' || isRecord(value.volumes) && Object.hasOwn(value.volumes, source));
      builder.edge(from.id, to.id, 'uses', '声明挂载', [evidence(file.path, line, 'compose-declaration', '配置挂载声明；未读取挂载目标。')]);
    }
  }
}

export async function analyzeProject({ projectRoot, ...options }) {
  const root = await canonicalRoot(projectRoot);
  const limits = { ...DEFAULT_LIMITS, ...(options.limits ?? {}) };
  for (const key of Object.keys(DEFAULT_LIMITS)) if (!Number.isInteger(limits[key]) || limits[key] < 1 || limits[key] > DEFAULT_LIMITS[key] * 4) throw new Error('SCAN_LIMIT_INVALID');
  const scanned = await scan(root, limits);
  const gaps = [...scanned.gaps];
  const project = { id: stableId('project', process.platform === 'win32' ? root.toLowerCase() : root), name: basename(root), root };
  const builder = createBuilder(project.id, scanned.files);
  const resolveImport = jsResolver(scanned.files, builder, gaps);
  for (const file of scanned.files) {
    if (file.language === 'javascript' || file.language === 'typescript') parseJavaScript(file, builder, resolveImport, gaps);
    else if (file.language === 'compose') parseCompose(file, builder, gaps);
    else if (file.language.startsWith('unsupported:') || file.language === 'python-manifest') gaps.push(gap('ADAPTER_UNSUPPORTED', file.path, `仅登记文件，尚未解析 ${file.language.replace('unsupported:', '')} 语义。`));
    else if (file.language === 'package-json') {
      try {
        const manifest = JSON.parse(file.text);
        for (const group of ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']) for (const name of Object.keys(manifest[group] ?? {})) {
          const pin = evidence(file.path, Math.max(1, file.text.slice(0, file.text.indexOf(`"${name}"`)).split('\n').length), 'manifest-dependency', `package.json ${group} 声明。`);
          const dependency = builder.node('external', `package:${name}`, name, { attributes: { observation: 'declared', package: true }, evidence: [pin] });
          builder.edge(builder.fileMap.get(file.path).id, dependency.id, 'depends-on', group, [pin]);
        }
      } catch { gaps.push(gap('MANIFEST_PARSE_ERROR', file.path, 'package.json 无法解析。')); }
    }
  }
  const pythonFiles = scanned.files.filter((file) => file.language === 'python');
  const python = pythonFiles.length ? await parsePython(pythonFiles, options) : { available: null, files: [] };
  if (python.available) addPythonResults(python.files, scanned.files, builder, gaps);
  else if (pythonFiles.length) gaps.push(gap('PYTHON_UNAVAILABLE', '', 'Python AST 不可用。请设置 PANORAMA_PYTHON 为 Python 3.9+ 解释器；当前仅显示 Python 文件。'));
  if (scanned.files.some((file) => CODE_LANGUAGES.has(extname(file.path)))) gaps.push({ ...gap('STATIC_ANALYSIS_BOUNDARY', '', '导入、符号与路由仅为静态声明；未解析反射、依赖注入、跨服务调用或真实时序。'), severity: 'informational' });
  await options.beforeVerify?.();
  const verification = await scan(root, limits);
  const fileIdentity = (value) => JSON.stringify(value.files.map(({ path, digest, size, language }) => ({ path, digest, size, language })));
  if (fileIdentity(scanned) !== fileIdentity(verification) || JSON.stringify(scanned.inventory) !== JSON.stringify(verification.inventory)) throw new Error('SOURCE_CHANGED_DURING_SCAN');
  const files = scanned.files.map(({ text, ...file }) => file);
  const contentDigest = digest(JSON.stringify(files));
  const capabilityLanguages = [...new Set(files.map((file) => file.language))].sort();
  const capabilities = capabilityLanguages.map((language) => {
    const count = files.filter((file) => file.language === language).length;
    const unsupported = language.startsWith('unsupported:') || language === 'python-manifest';
    const failedPython = language === 'python' && !python.available;
    const partial = gaps.some((issue) => issue.severity !== 'informational' && files.some((file) => file.language === language && file.path === issue.path));
    return { language, status: unsupported ? 'unsupported' : failedPython ? 'unavailable' : partial ? 'partial' : 'parsed', files: count, detail: language === 'python' ? (python.available ? 'Python stdlib AST：导入、符号、FastAPI/Flask 字面量路由；不分析运行顺序。' : '仅文件登记；需配置 Python AST 解释器。') : ['javascript', 'typescript'].includes(language) ? 'TypeScript AST：静态依赖、符号、Express 字面量路由；不证明调用。' : language === 'compose' ? 'Compose 服务、端口、依赖、挂载、网络声明；不读取环境变量值，不证明部署。' : unsupported ? '文件已登记，语义未解析。' : '读取静态配置声明。' };
  });
  const snapshotId = digest(JSON.stringify({ contentDigest, parserVersion: PARSER_VERSION, limits, capabilities: capabilities.map(({ language, status }) => ({ language, status })), omissions: gaps.filter((item) => /LIMIT|SKIPPED/.test(item.code)) }));
  return { schemaVersion: 'panorama.source.v1', project, analysisConfig: { limits }, snapshot: { id: snapshotId, contentDigest, parserVersion: PARSER_VERSION, generatedAt: new Date().toISOString() }, nodes: builder.nodes, edges: builder.edges, files, capabilities, gaps };
}

export async function readSourceEvidence(model, { path, line = 1, limit = 80 }) {
  const file = model.files.find((item) => item.path === path);
  if (!file || secretPath(path)) throw new Error('SOURCE_NOT_REGISTERED');
  if (!Number.isInteger(line) || line < 1 || !Number.isInteger(limit) || limit < 1 || limit > 200) throw new Error('SOURCE_RANGE_INVALID');
  const root = await canonicalRoot(model.project.root);
  if (root !== model.project.root) throw new Error('SOURCE_ROOT_CHANGED');
  const body = await safeFile(root, path, DEFAULT_LIMITS.maxFileBytes * 4);
  const currentDigest = digest(body);
  return { path, line, text: body.toString('utf8').split(/\r?\n/).slice(line - 1, line - 1 + limit).join('\n'), stale: currentDigest !== file.digest, digest: currentDigest };
}
