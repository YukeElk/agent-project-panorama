"""Parse supplied Python text without importing or executing project code."""
import ast
import json
import sys


def parse_file(item):
    result = {"path": item["path"], "imports": [], "symbols": [], "routes": [], "gaps": []}
    try:
        tree = ast.parse(item["text"], filename=item["path"])
    except (SyntaxError, ValueError) as error:
        result["gaps"].append({"code": "PYTHON_PARSE_ERROR", "line": getattr(error, "lineno", 1), "message": "Python syntax could not be parsed."})
        return result
    constructors = set()
    receivers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in {"fastapi", "flask"}:
                for alias in node.names:
                    if alias.name in {"FastAPI", "APIRouter", "Flask", "Blueprint"}:
                        constructors.add(alias.asname or alias.name)
            result["imports"].append({"module": node.module or "", "level": node.level, "names": [alias.name for alias in node.names], "line": node.lineno, "kind": "imports"})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append({"module": alias.name, "level": 0, "names": [], "line": node.lineno, "kind": "imports"})
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in constructors:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    receivers.add(target.id)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def declaration(self, node, kind):
            name = ".".join(self.scope + [node.name])
            result["symbols"].append({"name": node.name, "qualifiedName": name, "line": node.lineno, "kind": kind})
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                func = decorator.func
                if func.attr not in {"get", "post", "put", "patch", "delete", "options", "head", "route"}:
                    continue
                if not isinstance(func.value, ast.Name) or func.value.id not in receivers:
                    continue
                if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                    result["routes"].append({"method": func.attr.upper(), "route": decorator.args[0].value, "line": decorator.lineno, "handler": name})
                else:
                    result["gaps"].append({"code": "DYNAMIC_ROUTE", "line": decorator.lineno, "message": "Nonliteral route declaration was not resolved."})
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            self.declaration(node, "function")

        def visit_AsyncFunctionDef(self, node):
            self.declaration(node, "async-function")

        def visit_ClassDef(self, node):
            self.declaration(node, "class")

        def visit_Call(self, node):
            func = node.func
            dynamic = isinstance(func, ast.Name) and func.id == "__import__"
            dynamic = dynamic or (isinstance(func, ast.Attribute) and func.attr == "import_module" and isinstance(func.value, ast.Name) and func.value.id == "importlib")
            if dynamic:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    result["imports"].append({"module": node.args[0].value, "level": 0, "names": [], "line": node.lineno, "kind": "dynamic-import"})
                else:
                    result["gaps"].append({"code": "DYNAMIC_IMPORT", "line": node.lineno, "message": "Nonliteral dynamic import was not resolved."})
            self.generic_visit(node)

    Visitor().visit(tree)
    return result


def main():
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise ValueError("Invalid parser input")
    result = [parse_file(item) for item in payload["files"]]
    sys.stdout.write(json.dumps({"files": result}, ensure_ascii=True))


if __name__ == "__main__":
    main()
