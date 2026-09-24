from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .catalog import BY_ID, get_model
from .paths import model_dir
from .textutil import eta_seconds

STAMP = "reed-complete.json"
SKIP_NAMES = {".gitattributes"}
RESERVE_BYTES = 1536 * 1024 * 1024


class DownloadError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DownloadManager:
    def __init__(self, publish) -> None:
        self.publish = publish
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._tokens: dict[str, int] = {}
        self._stop = False

    def reconcile(self) -> None:
        for model_id in BY_ID:
            state = self._read_state(model_id)
            with self._lock:
                self._states[model_id] = state

    def snapshot(self, model_id: str) -> dict:
        with self._lock:
            state = dict(self._states.get(model_id) or self._read_state(model_id))
        return _public_state(state)

    def installed(self, model_id: str) -> bool:
        return self.snapshot(model_id)["state"] == "ready"

    def start(self, model_id: str, expected_bytes: int, files: list[dict]) -> dict:
        get_model(model_id)
        with self._lock:
            current = self._states.get(model_id) or self._read_state(model_id)
            if current["state"] == "downloading":
                return _public_state(current)
            if current["state"] == "ready":
                return _public_state(current)
        free = _free_bytes(model_dir(model_id).parent)
        needed = expected_bytes if expected_bytes > 0 else 0
        if needed and free < needed + RESERVE_BYTES:
            raise DownloadError(
                "Not enough free disk space. "
                f"Reed needs about {_fmt(needed)} for this checkpoint and wants "
                f"{_fmt(RESERVE_BYTES)} left afterward. {_fmt(free)} is free."
            )
        dest = model_dir(model_id)
        dest.mkdir(parents=True, exist_ok=True)
        if files:
            (dest / "reed-expected.json").write_text(json.dumps({"files": files, "bytes": expected_bytes}))
        _remove_stamp(dest)
        proc = _spawn(model_id, dest)
        with self._lock:
            token = self._tokens.get(model_id, 0) + 1
            self._tokens[model_id] = token
        state = {
            "state": "downloading",
            "received": measure_progress(dest),
            "expected": expected_bytes,
            "speed": None,
            "eta": None,
            "error": "",
            "started_at": time.time(),
            "samples": [(time.time(), measure_progress(dest))],
        }
        with self._lock:
            self._procs[model_id] = proc
            self._states[model_id] = state
        self._emit(model_id)
        thread = threading.Thread(target=self._watch, args=(model_id, proc, token), daemon=True)
        thread.start()
        return self.snapshot(model_id)

    def pause(self, model_id: str) -> dict:
        get_model(model_id)
        with self._lock:
            proc = self._procs.get(model_id)
            state = self._states.get(model_id)
        if proc is not None and proc.poll() is None:
            with self._lock:
                current = self._states.setdefault(model_id, {})
                current["state"] = "paused"
            _terminate(proc)
        dest = model_dir(model_id)
        _remove_stamp(dest)
        received = measure_progress(dest) if dest.exists() else 0
        with self._lock:
            current = self._states.get(model_id) or {}
            current.update(
                {
                    "state": "paused",
                    "received": received,
                    "error": "",
                    "speed": None,
                    "eta": None,
                }
            )
            self._states[model_id] = current
            self._procs.pop(model_id, None)
        self._emit(model_id)
        return self.snapshot(model_id)

    def remove(self, model_id: str) -> dict:
        get_model(model_id)
        with self._lock:
            proc = self._procs.get(model_id)
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        dest = model_dir(model_id)
        if dest.exists():
            import shutil

            shutil.rmtree(dest)
        with self._lock:
            self._procs.pop(model_id, None)
            self._states[model_id] = {
                "state": "missing",
                "received": 0,
                "expected": 0,
                "speed": None,
                "eta": None,
                "error": "",
            }
        self._emit(model_id)
        return self.snapshot(model_id)

    def _watch(self, model_id: str, proc: subprocess.Popen, token: int) -> None:
        dest = model_dir(model_id)
        expected = 0
        with self._lock:
            expected = int((self._states.get(model_id) or {}).get("expected") or 0)
        while proc.poll() is None:
            if self._stop or not self._current(model_id, token):
                return
            received = measure_progress(dest)
            now = time.time()
            if not self._current(model_id, token):
                return
            with self._lock:
                state = self._states.setdefault(model_id, {})
                if state.get("state") == "paused":
                    return
                samples = list(state.get("samples") or [])
                samples.append((now, received))
                samples = samples[-40:]
                remaining = max(expected - received, 0) if expected else 0
                state.update(
                    {
                        "state": "downloading",
                        "received": received,
                        "expected": expected,
                        "samples": samples,
                        "speed": _speed(samples),
                        "eta": eta_seconds(samples, remaining),
                        "error": "",
                    }
                )
            self._emit(model_id)
            time.sleep(0.4)
        code = proc.wait()
        if not self._current(model_id, token):
            return
        with self._lock:
            self._procs.pop(model_id, None)
            state = self._states.get(model_id) or {}
            if state.get("state") == "paused":
                return
        if code != 0:
            err = _log_tail(dest)
            message = err.strip() or f"Download process exited with status {code}."
            with self._lock:
                state = self._states.setdefault(model_id, {})
                state.update(
                    {
                        "state": "error",
                        "received": measure_progress(dest),
                        "error": message,
                        "speed": None,
                        "eta": None,
                    }
                )
            self._emit(model_id)
            return
        with self._lock:
            state = self._states.setdefault(model_id, {})
            state.update({"state": "verifying", "speed": None, "eta": None, "error": ""})
        self._emit(model_id)
        try:
            _verify(dest, expected)
        except DownloadError as exc:
            with self._lock:
                state = self._states.setdefault(model_id, {})
                state.update(
                    {
                        "state": "error",
                        "received": measure_progress(dest),
                        "error": exc.message,
                        "speed": None,
                        "eta": None,
                    }
                )
            self._emit(model_id)
            return
        with self._lock:
            state = self._states.setdefault(model_id, {})
            state.update(
                {
                    "state": "ready",
                    "received": expected or measure_progress(dest),
                    "expected": expected,
                    "error": "",
                    "speed": None,
                    "eta": None,
                }
            )
        self._emit(model_id)

    def _read_state(self, model_id: str) -> dict:
        dest = model_dir(model_id)
        expected = _expected_bytes(dest)
        if _stamp_ok(dest):
            return {
                "state": "ready",
                "received": expected or measure_progress(dest),
                "expected": expected,
                "speed": None,
                "eta": None,
                "error": "",
            }
        received = measure_progress(dest) if dest.exists() else 0
        if received > 0:
            return {
                "state": "paused",
                "received": received,
                "expected": expected,
                "speed": None,
                "eta": None,
                "error": "",
            }
        return {
            "state": "missing",
            "received": 0,
            "expected": expected,
            "speed": None,
            "eta": None,
            "error": "",
        }

    def _current(self, model_id: str, token: int) -> bool:
        with self._lock:
            return self._tokens.get(model_id) == token

    def _emit(self, model_id: str) -> None:
        self.publish({"type": "download", "model_id": model_id, "download": self.snapshot(model_id)})


def measure_received(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".metadata") or name.endswith(".lock") or name == ".DS_Store":
            continue
        if name in {STAMP, "reed-expected.json"}:
            continue
        total += path.stat().st_size
    return total


def measure_progress(root: Path) -> int:
    """Bytes received without counting a finished file and its temporary copy twice."""
    files = _expected_files(root)
    if not files:
        return measure_received(root)
    got = 0
    expected_total = 0
    for item in files:
        rel = item.get("path") or ""
        size = int(item.get("bytes") or 0)
        if not rel or rel.endswith(".md") or Path(rel).name in SKIP_NAMES:
            continue
        expected_total += size
        target = (root / rel)
        if target.is_file():
            actual = target.stat().st_size
            got += min(actual, size) if size else actual
    incomplete = 0
    cache = root / ".cache"
    if cache.exists():
        for path in cache.rglob("*"):
            if path.is_file() and path.name.endswith(".incomplete"):
                incomplete += path.stat().st_size
    missing = max(expected_total - got, 0)
    return got + min(incomplete, missing)


def _verify(dest: Path, expected: int) -> None:
    manifest_path = dest / "reed-expected.json"
    files = []
    if manifest_path.exists():
        try:
            files = json.loads(manifest_path.read_text()).get("files") or []
        except (OSError, json.JSONDecodeError):
            files = []
    missing = []
    for item in files:
        rel = item.get("path") or ""
        if not rel or rel.endswith(".md") or Path(rel).name in SKIP_NAMES:
            continue
        target = (dest / rel).resolve()
        if dest.resolve() not in target.parents and target != dest.resolve():
            missing.append(rel)
            continue
        size = int(item.get("bytes") or 0)
        if not target.is_file():
            missing.append(rel)
            continue
        if size and target.stat().st_size != size:
            missing.append(rel)
    if missing:
        raise DownloadError(
            "The checkpoint is incomplete, so Reed will not mark it ready. Missing or mismatched: "
            + ", ".join(missing[:6])
        )
    weight = dest / "model.safetensors"
    tokenizer = dest / "speech_tokenizer" / "model.safetensors"
    config = dest / "config.json"
    if not weight.is_file() or weight.stat().st_size < 100_000_000:
        raise DownloadError("The checkpoint is missing model weights, so Reed will not mark it ready.")
    if not tokenizer.is_file() or tokenizer.stat().st_size < 50_000_000:
        raise DownloadError("The checkpoint is missing its speech tokenizer, so Reed will not mark it ready.")
    if not config.is_file():
        raise DownloadError("The checkpoint is missing config.json, so Reed will not mark it ready.")
    received = measure_progress(dest)
    if expected and received + 4096 < int(expected * 0.98):
        raise DownloadError(
            f"Downloaded {_fmt(received)} of the expected {_fmt(expected)}. Reed will not mark an incomplete checkpoint ready."
        )
    stamp = {
        "ready": True,
        "bytes": received,
        "expected": expected,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (dest / STAMP).write_text(json.dumps(stamp))


def _stamp_ok(dest: Path) -> bool:
    stamp_path = dest / STAMP
    weight = dest / "model.safetensors"
    tokenizer = dest / "speech_tokenizer" / "model.safetensors"
    config = dest / "config.json"
    if not stamp_path.is_file() or not weight.is_file() or not tokenizer.is_file() or not config.is_file():
        return False
    try:
        stamp = json.loads(stamp_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not stamp.get("ready"):
        return False
    if weight.stat().st_size < 100_000_000 or tokenizer.stat().st_size < 50_000_000:
        return False
    return True


def _expected_files(dest: Path) -> list[dict]:
    path = dest / "reed-expected.json"
    if not path.is_file():
        return []
    try:
        files = json.loads(path.read_text()).get("files") or []
    except (OSError, json.JSONDecodeError):
        return []
    return files if isinstance(files, list) else []


def _expected_bytes(dest: Path) -> int:
    path = dest / "reed-expected.json"
    if not path.is_file():
        return 0
    try:
        return int(json.loads(path.read_text()).get("bytes") or 0)
    except (OSError, json.JSONDecodeError, ValueError):
        return 0


def _remove_stamp(dest: Path) -> None:
    stamp = dest / STAMP
    if stamp.exists():
        stamp.unlink()


def _spawn(model_id: str, dest: Path) -> subprocess.Popen:
    worker = Path(__file__).resolve().parent / "download_worker.py"
    env = os.environ.copy()
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    log_path = dest / "download.log"
    log = open(log_path, "w", encoding="utf-8")
    try:
        return subprocess.Popen(
            [os.environ.get("REED_PYTHON", _python()), str(worker), model_id, str(dest)],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
    finally:
        log.close()


def _python() -> str:
    return os.environ.get("REED_PYTHON") or _default_python()


def _default_python() -> str:
    candidate = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return "python3"


def _terminate(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 15)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()


def _speed(samples: list[tuple[float, int]]) -> float | None:
    if len(samples) < 2:
        return None
    now_t, now_b = samples[-1]
    earlier = samples[0]
    cutoff = now_t - 5
    for sample in samples:
        if sample[0] >= cutoff:
            earlier = sample
            break
    elapsed = now_t - earlier[0]
    received = now_b - earlier[1]
    if elapsed < 0.4 or received <= 0:
        return None
    return received / elapsed


def _public_state(state: dict) -> dict:
    expected = int(state.get("expected") or 0)
    received = int(state.get("received") or 0)
    percent = None
    if expected > 0 and state.get("state") == "downloading":
        percent = max(0.0, min(100.0, received / expected * 100))
    return {
        "state": state.get("state") or "missing",
        "received": received,
        "expected": expected,
        "percent": percent,
        "speed": state.get("speed"),
        "eta": state.get("eta"),
        "error": state.get("error") or "",
    }


def _free_bytes(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    import shutil

    return shutil.disk_usage(path).free


def _log_tail(dest: Path) -> str:
    path = dest / "download.log"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-1200:]


def _fmt(size: int) -> str:
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    return f"{size / 1_000_000:.0f} MB"
