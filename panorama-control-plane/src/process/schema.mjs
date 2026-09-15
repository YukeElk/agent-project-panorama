import { readFileSync } from 'node:fs';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { processValue } from './json.mjs';
import { requireProcess } from './errors.mjs';

const contractRoot = new URL('../../contracts/process/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('schema-manifest.json', contractRoot), 'utf8'));
// Only bundled schemas are compiled. Imported packs contain data, never schemas/code.
const ajv = new Ajv2020({ strictSchema: true, strictTypes: false, strictRequired: false, allErrors: false, allowUnionTypes: true, validateFormats: true, ownProperties: true });
addFormats(ajv, { mode: 'full' });
for (const entry of manifest.schemas) ajv.addSchema(JSON.parse(readFileSync(new URL(entry.path, contractRoot), 'utf8')));
const formats = new Map([
  ['panorama.process-project.v1', 'project:1'], ['standard-pack.v0.2', 'standard-pack:2'],
  ['panorama.process-receipt.v1', 'receipt:1'], ['panorama.process-assessment.v1', 'assessment:1'],
]);
const validators = new Map([...formats].map(([format, id]) => [format, ajv.getSchema('urn:panorama:process:schema:' + id)]));

export function validateDocument(input, expectedFormat) {
  const value = processValue(input);
  requireProcess(value && typeof value === 'object' && !Array.isArray(value), 'DOCUMENT_REQUIRED');
  if (expectedFormat) requireProcess(value.formatVersion === expectedFormat, 'FORMAT_MISMATCH');
  const validate = validators.get(value.formatVersion);
  requireProcess(validate, 'FORMAT_UNSUPPORTED');
  requireProcess(validate(value), 'SCHEMA_INVALID', { issues: (validate.errors ?? []).slice(0, 8).map(error => ({ path: error.instancePath, keyword: error.keyword })) });
  return value;
}

const definitions = new Map();
export function validateDefinition(input, name) {
  const value = processValue({ value: input }).value;
  let validate = definitions.get(name);
  if (!validate) {
    validate = ajv.getSchema('urn:panorama:process:schema:common:1#/$defs/' + name);
    requireProcess(validate, 'DEFINITION_UNSUPPORTED');
    definitions.set(name, validate);
  }
  requireProcess(validate(value), 'SCHEMA_INVALID', { definition: name });
  return value;
}
