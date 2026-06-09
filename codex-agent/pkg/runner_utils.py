"""Shared helpers for the Codex runner."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import typing

DEFAULT_CONTEXT_DIRECTORY = ".langbot/agent-runner"
MAX_CAPTURED_OUTPUT_CHARS = 128_000

_PROTECTED_POSIX_ROOTS = {
    "/",
    "/Users",
    "/Users/Shared",
    "/home",
    "/root",
    "/var",
    "/etc",
    "/tmp",
    "/usr",
    "/opt",
}
_BLOCKED_ENV_KEYS = {
    "HOME",
    "PATH",
    "USER",
    "SHELL",
    "TERM",
    "CODEX_HOME",
    "LANGBOT_REMOTE_MCP_DAEMON_ENDPOINT",
    "LANGBOT_REMOTE_MCP_RUN_ID",
    "LANGBOT_REMOTE_MCP_SECRET",
    "PYTHONPATH",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|credential|password|secret|token)"
    r"(\s*[:=]\s*)"
    r"([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_INHERITED_ENV_KEYS = {
    "ALL_PROXY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "CURL_CA_BUNDLE",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_PROXY",
    "NODE_EXTRA_CA_CERTS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "VERTEXAI_LOCATION",
    "VERTEXAI_PROJECT",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}


def to_bool(value: typing.Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def parse_args(value: typing.Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def parse_positive_int(value: typing.Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def safe_name(value: typing.Any, fallback: str = "item") -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    return (text or fallback)[:96]


def dump_jsonable(value: typing.Any) -> typing.Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): dump_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [dump_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def loads_json_config(value: typing.Any, field_name: str) -> typing.Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"{field_name} must be valid JSON: {e}") from e
    return value


def parse_config_overrides(value: typing.Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return shlex.split(stripped)
        return parse_config_overrides(parsed)
    if isinstance(value, dict):
        return [f"{key}={toml_literal(item)}" for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def is_codex_mcp_config_override(value: typing.Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    key = text.split("=", 1)[0].strip()
    return key == "mcp_servers" or key.startswith("mcp_servers.")


def filter_codex_config_overrides(values: list[str]) -> list[str]:
    return [item for item in values if not is_codex_mcp_config_override(item)]


def toml_literal(value: typing.Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {toml_literal(item)}" for key, item in value.items()) + " }"
    return json.dumps(value, ensure_ascii=False)


def _is_drive_root(path: pathlib.Path) -> bool:
    text = str(path)
    return os.name == "nt" and bool(path.drive) and text.rstrip("\\/") == path.drive.rstrip("\\/")


def _protected_roots() -> set[str]:
    roots = set(_PROTECTED_POSIX_ROOTS)
    if os.name == "nt":
        roots.update(
            {
                r"C:\Users",
                r"C:\ProgramData",
                r"C:\Program Files",
                r"C:\Program Files (x86)",
                r"C:\Windows",
            }
        )
    return roots


def normalize_working_directory(value: typing.Any, *, fallback: str | None = None) -> str:
    text = str(value or fallback or "").strip()
    if not text:
        text = os.getcwd()

    candidate = pathlib.Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = pathlib.Path.cwd() / candidate
    candidate = candidate.resolve(strict=True)

    if not candidate.is_dir():
        raise ValueError(f"working directory is not a directory: {candidate}")
    if _is_drive_root(candidate):
        raise ValueError(f"working directory must not be a drive root: {candidate}")

    candidate_text = str(candidate)
    for protected in _protected_roots():
        try:
            protected_path = pathlib.Path(protected).resolve(strict=False)
        except OSError:
            protected_path = pathlib.Path(protected)
        if candidate_text == str(protected_path):
            raise ValueError(f"working directory must not be a protected system root: {candidate}")

    try:
        home = pathlib.Path.home().resolve(strict=False)
    except RuntimeError:
        home = None
    if home is not None and candidate_text == str(home):
        raise ValueError(f"working directory must not be the user home directory: {candidate}")

    return candidate_text


def resolve_under_workdir(working_directory: str, value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        path = pathlib.Path(working_directory) / path
    return path


def resolve_context_directory(working_directory: str, value: typing.Any) -> pathlib.Path:
    relative = safe_relative_posix_path(value)
    if not relative:
        raise ValueError("context directory must be a relative path inside the working directory")
    base = pathlib.Path(working_directory).resolve(strict=True)
    target = (base / pathlib.PurePosixPath(relative)).resolve(strict=False)
    if not target.is_relative_to(base):
        raise ValueError("context directory must stay inside the working directory")
    return target


def safe_relative_posix_path(relative_path: typing.Any) -> str | None:
    path = pathlib.PurePosixPath(str(relative_path or ""))
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        return None
    return str(path)


def is_blocked_env_key(key: str) -> bool:
    upper = key.strip().upper()
    return upper.startswith("LANGBOT_") or upper in _BLOCKED_ENV_KEYS


def _dedupe_path_entries(entries: typing.Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        if entry and entry not in seen:
            result.append(entry)
            seen.add(entry)
    return result


def inherited_harness_env() -> dict[str, str]:
    """Return the small host env surface needed by external harness CLIs."""

    env = {key: value for key, value in os.environ.items() if key in _INHERITED_ENV_KEYS}

    home = env.get("HOME") or str(pathlib.Path.home())
    if home:
        env["HOME"] = home
        env.setdefault("USERPROFILE", home)

    path_entries = [
        str(pathlib.Path(home) / ".local" / "bin") if home else "",
        str(pathlib.Path(home) / ".npm-global" / "bin") if home else "",
        env.get("PATH", ""),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    env["PATH"] = os.pathsep.join(_dedupe_path_entries(path_entries))
    return env


def shared_codex_home() -> pathlib.Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    if configured:
        return pathlib.Path(configured).expanduser().resolve(strict=False)
    return pathlib.Path.home().joinpath(".codex").resolve(strict=False)


def strip_codex_mcp_servers_config(content: str) -> str:
    if "mcp_servers" not in content:
        return content

    result: list[str] = []
    in_mcp_servers = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            header = stripped.lstrip("[").rstrip("]").strip()
            in_mcp_servers = header == "mcp_servers" or header.startswith("mcp_servers.")
            if in_mcp_servers:
                continue
        if in_mcp_servers:
            continue
        result.append(line)

    cleaned = "\n".join(result).strip()
    return f"{cleaned}\n" if cleaned else ""


def _remove_existing_path(path: pathlib.Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return


def _link_or_copy_file(src: pathlib.Path, dst: pathlib.Path) -> None:
    if not src.exists():
        return
    _remove_existing_path(dst)
    try:
        dst.symlink_to(src)
    except OSError:
        shutil.copy2(src, dst)
        try:
            dst.chmod(0o600)
        except OSError:
            pass


def _link_directory(src: pathlib.Path, dst: pathlib.Path) -> None:
    src.mkdir(parents=True, exist_ok=True)
    _remove_existing_path(dst)
    try:
        dst.symlink_to(src, target_is_directory=True)
    except OSError:
        dst.mkdir(parents=True, exist_ok=True)


def _sync_shared_file(src: pathlib.Path, dst: pathlib.Path, *, sanitize_mcp: bool = False) -> None:
    _remove_existing_path(dst)
    if not src.exists():
        return
    if sanitize_mcp:
        content = strip_codex_mcp_servers_config(src.read_text(encoding="utf-8"))
        if content:
            dst.write_text(content, encoding="utf-8")
    else:
        shutil.copy2(src, dst)
    try:
        dst.chmod(0o600)
    except OSError:
        pass


def prepare_codex_home(codex_home: pathlib.Path, *, managed_config: str = "") -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    shared_home = shared_codex_home()
    try:
        same_home = shared_home.resolve(strict=False) == codex_home.resolve(strict=False)
    except OSError:
        same_home = False

    if not same_home:
        _link_directory(shared_home / "sessions", codex_home / "sessions")
        _link_or_copy_file(shared_home / "auth.json", codex_home / "auth.json")
        _sync_shared_file(shared_home / "config.json", codex_home / "config.json")
        _sync_shared_file(shared_home / "instructions.md", codex_home / "instructions.md")
        _sync_shared_file(shared_home / "config.toml", codex_home / "config.toml", sanitize_mcp=True)

    config_path = codex_home / "config.toml"
    base_config = ""
    if config_path.exists():
        base_config = strip_codex_mcp_servers_config(config_path.read_text(encoding="utf-8")).strip()

    managed = managed_config.strip()
    if base_config or managed:
        content = "\n\n".join(part for part in (base_config, managed) if part) + "\n"
        config_path.write_text(content, encoding="utf-8")
        try:
            config_path.chmod(0o600)
        except OSError:
            pass


def redact_text(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _SENSITIVE_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)


def bounded_text(value: str, *, limit: int = MAX_CAPTURED_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def safe_output(value: str, *, limit: int = MAX_CAPTURED_OUTPUT_CHARS) -> str:
    return bounded_text(redact_text(value), limit=limit)


def subprocess_kwargs() -> dict[str, typing.Any]:
    if os.name != "nt":
        return {"start_new_session": True}
    return {}


def terminate_process(process: typing.Any) -> None:
    pid = getattr(process, "pid", None)
    if os.name != "nt" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def normalize_returncode(process: typing.Any) -> int:
    return int(getattr(process, "returncode", 0) or 0)
