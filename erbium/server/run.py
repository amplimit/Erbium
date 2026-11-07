from logging import INFO, basicConfig, getLogger
from os import PathLike, environ
from pathlib import Path
from socket import gethostname
from typing import IO, Literal

from uvicorn import run

from erbium.api import LinuxAccount, Node, Store
from erbium.server.app import create_app
from erbium.server.notification import Dispatcher, Notifications, Notifier, ResendNotifier, SilentNotifier

DEFAULT_STATE_DIR: str = "/var/lib/erbium"

logger = getLogger(__name__)


def open_store(state_dir: str | PathLike[str] = DEFAULT_STATE_DIR) -> Store:
    return Store(Path(state_dir) / "erbium.db")


def _notifier() -> Notifier:
    api_key = environ.get("RESEND_API_KEY")
    sender = environ.get("ERBIUM_MAIL_FROM")
    if api_key and sender:
        return ResendNotifier(api_key, sender)
    if api_key or sender:
        logger.warning("Both RESEND_API_KEY and ERBIUM_MAIL_FROM are required, notifications are disabled")
    return SilentNotifier()


def _single_instance(state_dir: Path) -> IO[str]:
    from fcntl import LOCK_EX, LOCK_NB, flock

    handle = open(state_dir / "server.lock", "w")
    try:
        flock(handle, LOCK_EX | LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit("Another Erbium server is already running with this state directory")
    return handle


def run_server(port: int, *, host: str = "0.0.0.0", state_dir: str | PathLike[str] = DEFAULT_STATE_DIR,
               jupyter_executable: str = "/workspace/venv/bin/jupyter",
               max_gpu_utilization: float = 15, max_gpu_memory_utilization: float = 15,
               log_level: Literal["critical", "error", "warning", "info", "debug", "trace"] = "warning",
               access_log: bool = False) -> None:
    basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    store = open_store(state_dir)
    lock = _single_instance(Path(state_dir))
    notifications = Notifications(store, Dispatcher(_notifier()), public_url=environ.get("ERBIUM_PUBLIC_URL"),
                                  node_name=environ.get("ERBIUM_NODE_NAME", gethostname()))
    node = Node(store, LinuxAccount(jupyter_executable=jupyter_executable), max_gpu_utilization=max_gpu_utilization,
                max_gpu_memory_utilization=max_gpu_memory_utilization, on_event=notifications.handle)
    node.start()
    try:
        run(create_app(node), host=host, port=port, log_level=log_level, access_log=access_log)
    finally:
        node.shutdown()
        lock.close()
