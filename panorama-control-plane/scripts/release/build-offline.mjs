#!/usr/bin/env node
// Build on a connected development host. The resulting bundle never installs packages.
import { readFile, readdir, lstat, realpath, mkdir, copyFile, writeFile, chmod } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { dirname, join, relative, resolve, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyCore } from '../../src/development/core.mjs';

const source = fileURLToPath(new URL('../../', import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const fail = (code) => { throw Object.assign(new Error(code), {code}); };
const contained = (root, path) => { const p = relative(root, path); return p && p !== '..' && !p.startsWith('..' + sep) && !isAbsolute(p); };
const compare = (a,b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0;
export const runtimePaths = [
  'src/cli.mjs', 'src/local-project.mjs', 'src/domain/canonical.mjs',
  'src/server/standalone-server.mjs', 'src/server/security.mjs',
  'src/standalone', 'src/development', 'src/process',
  'contracts/process', 'contracts/checkers',
  'vendor/structure-core', 'skills/panorama-development-process', 'dist/workbench',
];
const runtimePackages = ['ajv', 'ajv-formats', 'typescript', 'yaml'];
const browserPackages = ['react', 'react-dom', 'scheduler', 'lucide-react'];

async function packageDirectory(name, from = source) {
  const require = createRequire(join(from, 'package.json'));
  let entry;
  try { entry = require.resolve(name + '/package.json'); }
  catch { entry = require.resolve(name); }
  let current = dirname(await realpath(entry));
  for (;;) {
    try { if (JSON.parse(await readFile(join(current, 'package.json'), 'utf8')).name === name) return current; } catch {}
    const parent = dirname(current); if (parent === current) fail('PACKAGE_NOT_FOUND'); current = parent;
  }
}

export async function buildOffline({output}) {
  if (!output || !isAbsolute(output)) fail('ABSOLUTE_OUTPUT_REQUIRED');
  const target = resolve(output), canonicalSource = await realpath(source);
  if (target === canonicalSource || contained(canonicalSource, target) || contained(target, canonicalSource)) fail('BUNDLE_OUTPUT_CONFLICT');
  try { await lstat(target); fail('BUNDLE_OUTPUT_EXISTS'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  await verifyCore();
  await readFile(join(source, 'dist/workbench/index.html'));
  const pkg = JSON.parse(await readFile(join(source, 'package.json'), 'utf8'));
  await mkdir(target, {recursive: true});
  const app = join(target, 'app'), files = [], packages = new Map();
  async function copyTree(from, to, {dependency = false} = {}) {
    const info = await lstat(from);
    if (info.isSymbolicLink()) fail('BUNDLE_LINK_REJECTED');
    if (info.isDirectory()) {
      await mkdir(to, {recursive: true});
      for (const name of (await readdir(from)).sort()) {
        if (['__pycache__', '.git', 'node_modules'].includes(name) || name.endsWith('.pyc') || name.endsWith('.map')) continue;
        await copyTree(join(from,name),join(to,name),{dependency});
      }
    } else {
      if (!info.isFile() || files.length >= 20000) fail('BUNDLE_FILE_INVALID');
      if (dependency && /\.(node|exe|dll|so|dylib)$/i.test(from)) fail('BUNDLE_NATIVE_DEPENDENCY');
      await mkdir(dirname(to), {recursive: true}); await copyFile(from,to);
      const sha256 = hash(await readFile(to));
      if (sha256 !== hash(await readFile(from))) fail('BUNDLE_SOURCE_CHANGED');
      files.push({path:relative(app,to).split(sep).join('/'),sha256});
    }
  }
  for (const path of runtimePaths) await copyTree(join(source,path),join(app,path));
  async function addPackage(name, from = source) {
    const root = await packageDirectory(name,from);
    const info = JSON.parse(await readFile(join(root,'package.json'),'utf8'));
    if (packages.has(name)) { if (packages.get(name).version !== info.version) fail('DEPENDENCY_VERSION_CONFLICT'); return; }
    if (info.os || info.cpu || info.gypfile || info.optionalDependencies && Object.keys(info.optionalDependencies).length) fail('DEPENDENCY_PLATFORM_UNPROVEN');
    packages.set(name,{name,version:info.version,license:info.license ?? 'SEE LICENSE IN PACKAGE',usage:'server'});
    await copyTree(root,join(app,'node_modules',name),{dependency:true});
    for (const dependency of Object.keys(info.dependencies ?? {}).sort()) await addPackage(dependency,root);
  }
  for (const name of runtimePackages) await addPackage(name);
  const notices = ['# Third-party notices', '', 'The original license files remain with the components. Panorama does not relicense them.', '',
    '- Structure core: MIT; app/vendor/structure-core/LICENSE; pinned source and commit in vendor-manifest.json.', ''];
  for (const name of browserPackages) {
    let root;
    try { root = await packageDirectory(name); } catch (error) { if (name !== 'scheduler') throw error; root = await packageDirectory(name,await packageDirectory('react-dom')); }
    const info = JSON.parse(await readFile(join(root,'package.json'),'utf8'));
    packages.set(name,{name,version:info.version,license:info.license,usage:'compiled browser assets'});
    const licenses = (await readdir(root)).filter(name => /license|notice|copyright/i.test(name));
    if (!licenses.length) fail('BROWSER_LICENSE_MISSING');
    for (const license of licenses) await copyTree(join(root,license),join(app,'licenses',name.replaceAll('/','-'),license));
  }
  for (const row of packages.values()) notices.push(`- ${row.name} ${row.version}: ${row.license} (${row.usage})`);
  const runtimeInfo = {name:'panorama',version:pkg.version,private:true,type:'module',engines:pkg.engines,
    dependencies:Object.fromEntries(runtimePackages.map(name=>[name,packages.get(name).version]))};
  async function appText(path,text) { await mkdir(dirname(join(app,path)),{recursive:true}); await writeFile(join(app,path),text,{flag:'wx'}); files.push({path,sha256:hash(text)}); }
  await appText('package.json',JSON.stringify(runtimeInfo,null,2)+'\n');
  await appText('THIRD-PARTY-NOTICES.md',notices.join('\n')+'\n');
  await copyTree(join(source,'docs/OFFLINE.md'),join(app,'OFFLINE.md'));
  files.sort(compare);
  const manifest = {formatVersion:'panorama.offline-bundle.v1',version:pkg.version,nodeEngine:pkg.engines.node,python:'3.11+',
    platforms:['linux','win32'],architecture:'runtime-independent JavaScript and Python; use matching host runtimes',
    runtimesIncluded:false,packages:[...packages.values()].sort((a,b)=>a.name.localeCompare(b.name)),files};
  const raw = JSON.stringify(manifest,null,2)+'\n';
  await writeFile(join(target,'release.json'),raw,{flag:'wx'});
  const entry = (await readFile(new URL('./offline-entry.mjs',import.meta.url),'utf8')).replace("'__RELEASE_SHA256__'",JSON.stringify(hash(raw)));
  await writeFile(join(target,'offline.mjs'),entry,{flag:'wx'});
  for (const name of ['install.sh','install.ps1']) { await copyFile(new URL('./'+name,import.meta.url),join(target,name)); if (name.endsWith('.sh')) await chmod(join(target,name),0o755); }
  await copyFile(join(source,'docs/OFFLINE.md'),join(target,'README.md'));
  return {output:target,version:pkg.version,files:files.length,packages:manifest.packages,manifestSha256:hash(raw),runtimesIncluded:false};
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { if (process.argv.length !== 4 || process.argv[2] !== '--output') fail('USAGE_BUILD_OFFLINE_OUTPUT'); console.log(JSON.stringify(await buildOffline({output:process.argv[3]}),null,2)); }
  catch(error) { console.error(JSON.stringify({error:error.code ?? 'BUNDLE_FAILED',message:error.message})); process.exitCode=1; }
}
