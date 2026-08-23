"""Deterministic multistack registry and bounded declaration scanners.

The module returns syntax candidates and manifest declarations only.  It does
not execute project code, resolve runtime behavior, or promote source concepts
to formal Panorama modules.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from panorama_io import compute_canonical_hash


REGISTRY_ID = "panorama-multistack-adapter-registry"
REGISTRY_VERSION = "0.85.0"

LANGUAGE_ADAPTERS: dict[str, dict[str, Any]] = {
    "python": {"id": "python-ast-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "python_ast", "suffixes": [".py"]},
    "javascript": {"id": "javascript-static-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_regex", "suffixes": [".js", ".jsx", ".mjs", ".cjs"]},
    "typescript": {"id": "javascript-static-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_regex", "suffixes": [".ts", ".tsx"]},
    "java": {"id": "java-static-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_regex", "suffixes": [".java"]},
    "kotlin": {"id": "kotlin-bounded-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_lexer", "suffixes": [".kt", ".kts"]},
    "go": {"id": "go-bounded-imports", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_lexer", "suffixes": [".go"]},
    "csharp": {"id": "csharp-bounded-usings", "version": "0.1.0", "level": "L1", "status": "supported", "parser": "bounded_lexer", "suffixes": [".cs"]},
    "rust": {"id": "rust-bounded-uses", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".rs"]},
    "php": {"id": "php-bounded-uses", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".php"]},
    "ruby": {"id": "ruby-bounded-requires", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".rb"]},
    "swift": {"id": "swift-bounded-imports", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".swift"]},
    "scala": {"id": "scala-bounded-imports", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".scala"]},
    "c": {"id": "c-cpp-bounded-includes", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".c", ".h"]},
    "cpp": {"id": "c-cpp-bounded-includes", "version": "0.1.0", "level": "L1", "status": "preview", "parser": "bounded_lexer", "suffixes": [".cc", ".cpp", ".cxx", ".hpp", ".hxx"]},
}

MANIFEST_ADAPTERS: dict[str, dict[str, Any]] = {
    "package.json": {"language": "json", "id": "manifest-dependencies", "version": "0.3.0"},
    "pyproject.toml": {"language": "toml", "id": "manifest-dependencies", "version": "0.3.0"},
    "pom.xml": {"language": "xml", "id": "manifest-dependencies", "version": "0.3.0"},
    "go.mod": {"language": "go_mod", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "cargo.toml": {"language": "toml", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "composer.json": {"language": "json", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "gemfile": {"language": "ruby_manifest", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "package.swift": {"language": "swift_manifest", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "cmakelists.txt": {"language": "cmake", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "build.gradle": {"language": "gradle", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
    "build.gradle.kts": {"language": "gradle", "id": "multistack-manifest-dependencies", "version": "0.1.0"},
}

FRAMEWORK_DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("react", "frontend", "React"), ("next", "frontend", "Next.js"),
    ("vue", "frontend", "Vue"), ("vite", "build", "Vite"),
    ("express", "backend", "Express"), ("@nestjs/", "backend", "NestJS"),
    ("fastapi", "backend", "FastAPI"), ("django", "backend", "Django"),
    ("flask", "backend", "Flask"), ("sqlalchemy", "data", "SQLAlchemy"),
    ("celery", "messaging", "Celery"), ("spring-boot", "backend", "Spring Boot"),
    ("gin-gonic/gin", "backend", "Gin"), ("gofiber/fiber", "backend", "Fiber"),
    ("microsoft.aspnetcore", "backend", "ASP.NET Core"),
    ("entityframeworkcore", "data", "Entity Framework Core"),
    ("prisma", "data", "Prisma"), ("typeorm", "data", "TypeORM"),
    ("hibernate", "data", "Hibernate"), ("mybatis", "data", "MyBatis"),
    ("redis", "data", "Redis"), ("kafka", "messaging", "Kafka"),
    ("rabbitmq", "messaging", "RabbitMQ"), ("grpc", "interface", "gRPC"),
    ("graphql", "interface", "GraphQL"), ("openai", "ai", "OpenAI"),
    ("langchain", "ai", "LangChain"), ("llamaindex", "ai", "LlamaIndex"),
    ("llama-index", "ai", "LlamaIndex"), ("axum", "backend", "Axum"),
    ("actix", "backend", "Actix"), ("laravel", "backend", "Laravel"),
    ("rails", "backend", "Rails"),
)


def registry_projection() -> dict[str, Any]:
    value = {
        "id": REGISTRY_ID,
        "version": REGISTRY_VERSION,
        "languages": LANGUAGE_ADAPTERS,
        "manifests": MANIFEST_ADAPTERS,
        "frameworkDependencies": FRAMEWORK_DEPENDENCIES,
    }
    return {"id": REGISTRY_ID, "version": REGISTRY_VERSION, "hash": compute_canonical_hash(value)}


def source_suffix_map() -> dict[str, tuple[str, str]]:
    return {
        suffix: ("source", language)
        for language, entry in LANGUAGE_ADAPTERS.items()
        for suffix in entry["suffixes"]
    }


def manifest_name_map() -> dict[str, tuple[str, str]]:
    return {name: ("manifest", entry["language"]) for name, entry in MANIFEST_ADAPTERS.items()}


def manifest_suffix(path: Path) -> tuple[str, str] | None:
    name = path.name.casefold()
    if name.endswith(".csproj"):
        return "manifest", "csproj"
    return None


def configuration_kind(path: Path) -> tuple[str, str] | None:
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    posix = path.as_posix().casefold()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "configuration", "dockerfile"
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return "configuration", "yaml"
    if suffix == ".tf":
        return "configuration", "hcl"
    if suffix in {".yaml", ".yml"} and ("/.github/workflows/" in f"/{posix}" or name.startswith(("openapi", "swagger"))):
        return "configuration", "yaml"
    if suffix in {".proto", ".graphql", ".gql", ".sql"}:
        return "configuration", "unknown"
    return None


def _strip_c_comments(text: str) -> str:
    output: list[str] = []
    state = "normal"
    index = 0
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "line":
            if char == "\n":
                output.append(char)
                state = "normal"
            else:
                output.append(" ")
        elif state == "block":
            if char == "*" and nxt == "/":
                output.extend("  ")
                index += 1
                state = "normal"
            else:
                output.append("\n" if char == "\n" else " ")
        elif state in {"single", "double"}:
            output.append(char)
            if char == "\\" and nxt:
                output.append(nxt)
                index += 1
            elif (state == "single" and char == "'") or (state == "double" and char == '"'):
                state = "normal"
        elif char == "/" and nxt == "/":
            output.extend("  ")
            index += 1
            state = "line"
        elif char == "/" and nxt == "*":
            output.extend("  ")
            index += 1
            state = "block"
        else:
            output.append(char)
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
        index += 1
    return "".join(output)


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_language_source(language: str, body: bytes) -> dict[str, Any]:
    """Return package metadata and bounded dependency candidates."""
    text = body.decode("utf-8")
    cleaned = _strip_c_comments(text)
    dependencies: list[dict[str, Any]] = []
    package: str | None = None

    package_patterns = {
        "kotlin": r"(?m)^\s*package\s+([A-Za-z_][\w.]*)",
        "go": r"(?m)^\s*package\s+([A-Za-z_]\w*)",
        "csharp": r"(?m)^\s*namespace\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
        "php": r"(?m)^\s*namespace\s+([A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*)\s*;",
        "scala": r"(?m)^\s*package\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
    }
    if language in package_patterns:
        match = re.search(package_patterns[language], cleaned)
        if match:
            package = match.group(1)

    patterns: list[tuple[re.Pattern[str], str, bool]] = []
    if language in {"kotlin", "scala", "swift"}:
        patterns.append((re.compile(r"(?m)^\s*import\s+([A-Za-z_][\w.]*)"), "import", False))
    elif language == "csharp":
        patterns.append((re.compile(r"(?m)^\s*using\s+(?:static\s+)?(?:[A-Za-z_]\w*\s*=\s*)?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*;"), "import", False))
    elif language == "rust":
        patterns.extend([
            (re.compile(r"(?m)^\s*use\s+([^;]{1,400})\s*;"), "import", False),
            (re.compile(r"(?m)^\s*extern\s+crate\s+([A-Za-z_]\w*)\s*;"), "import", False),
            (re.compile(r"(?m)^\s*mod\s+([A-Za-z_]\w*)\s*;"), "import", False),
        ])
    elif language == "php":
        patterns.extend([
            (re.compile(r"(?m)^\s*use\s+([A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*)"), "import", False),
            (re.compile(r"(?m)^\s*(?:require|require_once|include|include_once)\s*\(?\s*(['\"])([^'\"]+)\1"), "dynamic_import", True),
        ])
    elif language == "ruby":
        patterns.append((re.compile(r"(?m)^\s*(require|require_relative)\s*\(?\s*(['\"])([^'\"]+)\2"), "import", False))
    elif language in {"c", "cpp"}:
        patterns.append((re.compile(r"(?m)^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]"), "import", False))

    if language == "go":
        for match in re.finditer(r"(?ms)^\s*import\s*(?:\((.*?)\)|(?:[A-Za-z_]\w*\s+)?\"([^\"]+)\")", cleaned):
            if match.group(2):
                dependencies.append({"specifier": match.group(2), "line": _line(cleaned, match.start()), "column": match.start() - cleaned.rfind("\n", 0, match.start()) - 1, "kind": "import", "dynamic": False})
            else:
                block = match.group(1) or ""
                base = match.start(1)
                for quoted in re.finditer(r"(?:[A-Za-z_]\w*\s+)?\"([^\"]+)\"", block):
                    dependencies.append({"specifier": quoted.group(1), "line": _line(cleaned, base + quoted.start()), "column": None, "kind": "import", "dynamic": False})
    else:
        for pattern, kind, dynamic in patterns:
            for match in pattern.finditer(cleaned):
                if language in {"php", "ruby", "c", "cpp"} and match.lastindex and match.lastindex >= 2:
                    specifier = match.group(match.lastindex)
                    if language == "ruby" and match.group(1) == "require_relative":
                        specifier = "./" + specifier
                    if language in {"c", "cpp"} and match.group(1) == '"':
                        specifier = "./" + specifier
                else:
                    specifier = match.group(1)
                dependencies.append({"specifier": specifier.strip(), "line": _line(cleaned, match.start()), "column": match.start() - cleaned.rfind("\n", 0, match.start()) - 1, "kind": kind, "dynamic": dynamic})

    return {"package": package, "dependencies": dependencies}


def scan_multistack_manifest(path: str, language: str, body: bytes) -> dict[str, Any]:
    """Parse supported non-v0.6 manifests into dependency declarations."""
    name = Path(path).name.casefold()
    text = body.decode("utf-8")
    dependencies: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    def add(specifier: str, line: int, dependency_class: str) -> None:
        value = specifier.strip()
        if value and len(value) <= 512:
            dependencies.append({"specifier": value, "line": line, "dependencyClass": dependency_class})

    if name == "go.mod":
        module = re.search(r"(?m)^\s*module\s+([^\s]+)", text)
        if module:
            metadata["module"] = module.group(1)
        for match in re.finditer(
            r"(?m)^\s*require\s+([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\s+v[^\s]+",
            text,
        ):
            add(match.group(1), _line(text, match.start()), "go_require")
        for match in re.finditer(r"(?m)^\s*([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\s+v[^\s]+", text):
            add(match.group(1), _line(text, match.start()), "go_require")
    elif name == "cargo.toml":
        section = None
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.strip("[]").casefold()
                continue
            if section in {"dependencies", "dev-dependencies", "build-dependencies"}:
                match = re.match(r"([A-Za-z0-9_-]+)\s*=", stripped)
                if match:
                    add(match.group(1), line_no, f"cargo_{section}")
    elif name.endswith(".csproj"):
        root = ET.fromstring(text)
        if "Sdk" in root.attrib:
            metadata["sdk"] = root.attrib["Sdk"]
        for node in root.iter():
            local = node.tag.rpartition("}")[2]
            if local in {"PackageReference", "FrameworkReference", "ProjectReference"}:
                value = node.attrib.get("Include") or node.attrib.get("Update")
                if value:
                    add(value, 1, local.casefold())
    elif name in {"build.gradle", "build.gradle.kts"}:
        for line_no, raw in enumerate(text.splitlines(), start=1):
            match = re.search(r"\b(api|implementation|compileOnly|runtimeOnly|testImplementation)\s*(?:\(|\s)\s*['\"]([^'\"]+)['\"]", raw)
            if match:
                coordinate = match.group(2).split(":")
                add(":".join(coordinate[:2]) if len(coordinate) >= 2 else coordinate[0], line_no, f"gradle_{match.group(1)}")
    elif name == "composer.json":
        value = json.loads(text)
        for group in ("require", "require-dev"):
            for dependency in sorted((value.get(group) or {}).keys()):
                if dependency != "php":
                    add(dependency, 1, f"composer_{group}")
    elif name == "gemfile":
        for line_no, raw in enumerate(text.splitlines(), start=1):
            match = re.match(r"\s*gem\s+['\"]([^'\"]+)['\"]", raw)
            if match:
                add(match.group(1), line_no, "ruby_gem")
    elif name == "package.swift":
        for match in re.finditer(r"\.package\s*\([^)]*?(?:url:\s*)?['\"]([^'\"]+)['\"]", text, re.S):
            add(match.group(1), _line(text, match.start()), "swift_package")
    elif name == "cmakelists.txt":
        for match in re.finditer(r"(?is)target_link_libraries\s*\(\s*[^\s)]+\s+([^)]*)\)", text):
            for value in re.findall(r"[A-Za-z_][A-Za-z0-9_:.-]*", match.group(1)):
                if value.casefold() not in {"private", "public", "interface", "debug", "optimized", "general"}:
                    add(value, _line(text, match.start()), "cmake_target_link")
    return {"dependencies": dependencies, "metadata": metadata}


def dependency_stack_matches(specifier: str) -> list[tuple[str, str]]:
    lowered = specifier.casefold().replace("_", "-")
    matches: list[tuple[str, str]] = []
    for needle, category, technology in FRAMEWORK_DEPENDENCIES:
        if needle in lowered:
            matches.append((category, technology))
    return sorted(set(matches))


def filename_stack_match(path: str) -> tuple[str, str] | None:
    posix = path.casefold()
    name = Path(path).name.casefold()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "deployment", "Docker"
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return "deployment", "Docker Compose"
    if posix.startswith(".github/workflows/") and Path(path).suffix.casefold() in {".yml", ".yaml"}:
        return "ci", "GitHub Actions"
    if Path(path).suffix.casefold() == ".tf":
        return "deployment", "Terraform"
    if Path(path).suffix.casefold() == ".proto":
        return "interface", "gRPC/Protobuf"
    if Path(path).suffix.casefold() in {".graphql", ".gql"}:
        return "interface", "GraphQL"
    if name.startswith(("openapi", "swagger")) and Path(path).suffix.casefold() in {".yml", ".yaml"}:
        return "interface", "OpenAPI"
    if Path(path).suffix.casefold() == ".sql":
        return "data", "SQL"
    return None
