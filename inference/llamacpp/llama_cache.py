"""Run llama-server with a private slot endpoint and one reusable SSD snapshot."""

import hashlib
import http.client
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


LOGGER = logging.getLogger("llama-cache")
LLAMA_SERVER = "/app/llama-server"
UPSTREAM_PORT = 8001
GET_ROUTES = {"/health", "/v1/health", "/metrics", "/props", "/models", "/v1/models"}
GENERATION_ROUTES = {
    "/completion", "/completions", "/v1/completions", "/chat/completions", "/v1/chat/completions",
    "/responses", "/v1/responses", "/v1/messages",
}
POST_ROUTES = GENERATION_ROUTES | {"/tokenize", "/detokenize", "/apply-template", "/v1/messages/count_tokens"}
FORWARDED_HEADERS = {"content-type", "content-length", "content-encoding", "cache-control", "retry-after"}
MAX_REQUEST_BYTES = 16 * 1024 * 1024


def snapshot_name(args: list[str]) -> str:
    model = Path(args[args.index("--model") + 1])
    identity = model.stat()
    version = subprocess.check_output([LLAMA_SERVER, "--version"], stderr=subprocess.STDOUT, timeout=10)
    config = json.dumps([args, identity.st_ino, identity.st_size, identity.st_mtime_ns, identity.st_ctime_ns])
    key = hashlib.sha256(version + config.encode()).hexdigest()
    return f"slot-{key}.bin"


def slot_request(action: str, filename: str, timeout: float) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=timeout)
    try:
        connection.request("POST", f"/slots/0?action={action}", json.dumps({"filename": filename}),
                           {"Content-Type": "application/json"})
        reply = connection.getresponse()
        if reply.status != 200:
            raise OSError(f"llama.cpp respondeu HTTP {reply.status}")
        return json.load(reply)
    finally:
        connection.close()


class SlotCache:
    def __init__(self, directory: Path, args: list[str], interval: int):
        self.directory = directory
        self.interval = interval
        self.snapshot = None
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.pending = False
        self.last_generation = 0.0
        self.last_save_attempt = 0.0
        self.restored_tokens = 0
        self.saved_tokens = 0
        self.saved_bytes = 0
        if interval > 0:
            try:
                directory.mkdir(mode=0o700, exist_ok=True)
                self.snapshot = directory / snapshot_name(args)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                LOGGER.warning("Cache em SSD indisponível: %s", exc)

    def status(self) -> dict:
        return {"enabled": self.snapshot is not None, "ready": self.ready.is_set(),
                "saved_tokens": self.saved_tokens, "restored_tokens": self.restored_tokens,
                "saved_bytes": self.saved_bytes}

    def restore(self) -> None:
        if self.snapshot is None:
            return
        try:
            for partial in self.directory.glob(".pending-*.bin"):
                partial.unlink(missing_ok=True)
            if self.snapshot.is_file() and not self.snapshot.is_symlink():
                reply = slot_request("restore", self.snapshot.name, timeout=25)
                self.restored_tokens = int(reply["n_restored"])
                self.saved_bytes = self.snapshot.stat().st_size
                LOGGER.info("Cache KV restaurado do SSD: %d tokens", self.restored_tokens)
        except (OSError, ValueError, KeyError, http.client.HTTPException) as exc:
            LOGGER.warning("Cache KV não restaurado, iniciando inferência normalmente: %s", exc)

    def save(self, *, force: bool = False) -> None:
        if self.snapshot is None or not self.lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            if not self.pending:
                return
            if not force and (now - self.last_generation < 2 or now - self.last_save_attempt < self.interval):
                return
            self.last_save_attempt = now
            self.save_snapshot()
        finally:
            self.lock.release()

    def save_snapshot(self) -> None:
        temporary = self.directory / f".pending-{uuid.uuid4().hex}.bin"
        try:
            reply = slot_request("save", temporary.name, timeout=25)
            tokens = int(reply["n_saved"])
            if tokens <= 0:
                self.pending = False
                return
            with temporary.open("rb") as partial:
                os.fsync(partial.fileno())
            temporary.replace(self.snapshot)
            self.saved_tokens = tokens
            self.saved_bytes = self.snapshot.stat().st_size
            self.pending = False
            for old in self.directory.glob("slot-*.bin"):
                if old != self.snapshot:
                    old.unlink(missing_ok=True)
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            LOGGER.info("Cache KV salvo no SSD: %d tokens, %d bytes", tokens, self.saved_bytes)
        except (OSError, ValueError, KeyError, http.client.HTTPException) as exc:
            LOGGER.warning("Falha ao gravar cache KV, a inferência continua: %s", exc)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Não consegui limpar o arquivo temporário do cache KV")

    def run(self) -> None:
        while not self.stop.is_set():
            connection = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=2)
            try:
                connection.request("GET", "/health")
                if connection.getresponse().status == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            self.stop.wait(0.3)
        if self.stop.is_set():
            return
        self.restore()
        self.ready.set()
        while not self.stop.wait(1):
            self.save()


class InferenceProxy(ThreadingHTTPServer):
    def __init__(self, cache: SlotCache):
        self.cache = cache
        super().__init__(("0.0.0.0", 8000), RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.route()

    def do_POST(self) -> None:
        self.route()

    def log_message(self, *_args) -> None:
        pass  # O próprio llama.cpp registra as rotas atendidas.

    def send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self) -> None:
        target = urlsplit(self.path)
        cache = self.server.cache
        if self.command == "GET" and target.path == "/kv-cache" and not target.netloc:
            self.send_json(200, cache.status())
            return
        allowed = GET_ROUTES if self.command == "GET" else POST_ROUTES
        if target.netloc or target.path not in allowed:
            self.send_json(404, {"error": "Endpoint indisponível"})
            return
        if not cache.ready.is_set():
            self.send_json(503, {"error": "Carregando modelo de inferência"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if not 0 <= size <= MAX_REQUEST_BYTES or self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"error": "Tamanho da requisição inválido"})
            return
        self.connection.settimeout(30)
        body = self.rfile.read(size) if size else None
        if target.path not in GENERATION_ROUTES:
            self.forward(body)
            return
        with cache.lock:
            # Um único slot. Se a geração falhar ou pedir para desativar o cache, não grave seu conteúdo.
            cache.pending = False
            if self.forward(body) and permits_cache(body):
                cache.pending = True
                cache.last_generation = time.monotonic()

    def forward(self, body: bytes | None) -> bool:
        header_names = ("Content-Type", "Accept", "Authorization")
        headers = {key: self.headers[key] for key in header_names if key in self.headers}
        connection = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=300)
        started = False
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in FORWARDED_HEADERS:
                    self.send_header(key, value)
            # http.client decodifica o framing chunked. O proxy delimita o corpo pelo fechamento.
            self.end_headers()
            started = True
            while chunk := response.read1(64 * 1024):
                self.wfile.write(chunk)
            return response.status == 200
        except (OSError, http.client.HTTPException):
            if not started:
                try:
                    self.send_json(502, {"error": "Motor de inferência indisponível"})
                except OSError:
                    pass
            return False
        finally:
            connection.close()


def permits_cache(body: bytes | None) -> bool:
    try:
        request = json.loads(body or b"{}")
        return isinstance(request, dict) and request.get("cache_prompt", True) is not False
    except (ValueError, UnicodeError):
        return False


def main(args: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    os.umask(0o077)
    directory = Path(os.environ.get("LLAMA_KV_SSD_PATH", "/database-internals/kv"))
    try:
        interval = max(0, int(os.environ.get("LLAMA_KV_SSD_INTERVAL", "30")))
    except ValueError:
        LOGGER.warning("LLAMA_KV_SSD_INTERVAL inválido; usando 30 segundos")
        interval = 30
    private_args = [*args, "--host", "127.0.0.1", "--port", str(UPSTREAM_PORT)]
    cache = SlotCache(directory, private_args, interval)
    if cache.snapshot is not None:
        private_args += ["--slot-save-path", str(directory)]

    def stop(_number, _frame):
        cache.stop.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with InferenceProxy(cache) as proxy, subprocess.Popen([LLAMA_SERVER, *private_args]) as llama:
        http_server = threading.Thread(target=proxy.serve_forever, daemon=True)
        worker = threading.Thread(target=cache.run, daemon=True)
        http_server.start()
        worker.start()
        while llama.poll() is None and not cache.stop.wait(0.25):
            pass
        cache.stop.set()
        proxy.shutdown()
        worker.join(timeout=27)
        if llama.poll() is None:
            cache.save(force=True)
            llama.terminate()
            try:
                llama.wait(timeout=5)
            except subprocess.TimeoutExpired:
                llama.kill()
                llama.wait()
            return 0
        return llama.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
