"""P0 test-only JSON Schema validation. No business rules, commands or network retrieval."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from importlib.metadata import version

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

ROOT = Path(__file__).resolve().parents[2] / 'contracts' / 'process'


def offline(uri):
    raise NoSuchResource(ref=uri)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    schemas = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((ROOT / 'schemas').glob('*.schema.json'))]
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
    registry = Registry(retrieve=offline).with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    formats = FormatChecker()
    if 'date-time' not in formats.checkers:
        raise RuntimeError('Install test/process/requirements.txt; date-time validation support is required.')
    validators = {s['$id']: Draft202012Validator(s, registry=registry, format_checker=formats) for s in schemas}
    requests = json.load(sys.stdin)
    results = []
    for request in requests:
        validator = validators[request['schemaId']]
        errors = list(validator.iter_errors(request['document']))
        results.append({'id': request['id'], 'valid': not errors, 'errors': [
            {'path': '/' + '/'.join(map(str, error.absolute_path)), 'keyword': error.validator, 'message': error.message}
            for error in errors[:8]
        ]})
    print(json.dumps({'validator': 'jsonschema', 'version': version('jsonschema'), 'draft': '2020-12',
                      'networkRetrieval': False, 'schemasChecked': len(schemas), 'results': results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
