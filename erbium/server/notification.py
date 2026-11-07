from dataclasses import dataclass
from datetime import datetime, timezone
from json import dumps
from logging import getLogger
from queue import Queue
from threading import Thread
from time import sleep
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from erbium.api.node import JobEvent, NotificationKind, Store

logger = getLogger(__name__)


@dataclass(frozen=True)
class Message(object):
    to: str
    subject: str
    text: str
    key: str


class Notifier(Protocol):
    def send(self, message: Message) -> None:
        ...


class SilentNotifier(object):
    def send(self, message: Message) -> None:
        logger.debug("Notifications disabled, dropping message to %s", message.to)


class ResendNotifier(object):
    ENDPOINT: str = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, *, timeout: float = 10) -> None:
        self.sender: str = sender
        self.timeout: float = timeout
        self._api_key: str = api_key

    def send(self, message: Message) -> None:
        request = Request(self.ENDPOINT, method="POST", data=dumps({
            "from": self.sender, "to": [message.to], "subject": message.subject, "text": message.text
        }).encode(), headers={
            "Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "User-Agent": "erbium",
            "Idempotency-Key": message.key
        })
        with urlopen(request, timeout=self.timeout) as response:
            response.read()


class Dispatcher(object):
    def __init__(self, notifier: Notifier, *, attempts: int = 4, backoff_seconds: float = 2) -> None:
        self.notifier: Notifier = notifier
        self.attempts: int = attempts
        self.backoff_seconds: float = backoff_seconds
        self._queue: Queue[Message] = Queue(maxsize=1000)
        self._thread: Thread = Thread(target=self._run, daemon=True, name="Notifications")
        self._thread.start()

    def submit(self, message: Message) -> None:
        if self._queue.full():
            logger.error("Notification queue is full, dropping message to %s", message.to)
            return
        self._queue.put_nowait(message)

    def _deliver(self, message: Message) -> None:
        for attempt in range(1, self.attempts + 1):
            try:
                self.notifier.send(message)
                return
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                logger.warning("Notification to %s failed with HTTP %d (attempt %d)", message.to, error.code, attempt)
                if not retryable:
                    return
            except (URLError, TimeoutError, OSError) as error:
                logger.warning("Notification to %s failed: %s (attempt %d)", message.to, error, attempt)
            if attempt < self.attempts:
                sleep(self.backoff_seconds * 2 ** (attempt - 1))
        logger.error("Giving up on notification to %s", message.to)

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            try:
                self._deliver(message)
            except Exception:
                logger.exception("Unexpected notification failure")
            finally:
                self._queue.task_done()


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Notifications(object):
    FINISHED_TEXT: dict[str, tuple[NotificationKind, str]] = {
        "expired": ("finished", "reached its requested run time and was stopped"),
        "admin_stopped": ("admin", "was stopped by an administrator"),
        "admin_removed": ("admin", "was removed from the queue by an administrator"),
    }

    def __init__(self, store: Store, dispatcher: Dispatcher, *, public_url: str | None = None,
                 node_name: str = "Erbium") -> None:
        self.store: Store = store
        self.dispatcher: Dispatcher = dispatcher
        self.public_url: str | None = public_url.rstrip("/") if public_url else None
        self.node_name: str = node_name

    def _compose(self, event: JobEvent) -> tuple[NotificationKind, str, str] | None:
        job = event.job
        label = f"\"{job.name}\" (#{job.id})"
        match event.kind:
            case "queued":
                queue = [queued.id for queued in self.store.queued_jobs()]
                detail = f" It is number {queue.index(job.id) + 1} in the queue." if job.id in queue else ""
                return "queued", f"Job {label} queued", f"Your job {label} has joined the queue.{detail}"
            case "started":
                ends_at = job.ends_at()
                assert ends_at is not None
                return "started", f"Your turn: job {label} is running", (
                    f"Your job {label} has started and will run until {_format_time(ends_at)}. "
                    "Open the dashboard to get your SSH password and Jupyter link."
                )
            case "ending":
                ends_at = job.ends_at()
                assert ends_at is not None
                return "ending", f"Job {label} ends soon", (
                    f"Your job {label} will be stopped at {_format_time(ends_at)}. All of your processes will be "
                    "killed at that time, so save checkpoints and results under /workspace/output now."
                )
            case "finished":
                if job.end_reason not in self.FINISHED_TEXT:
                    return None
                kind, reason = self.FINISHED_TEXT[job.end_reason]
                return kind, f"Job {label} ended", f"Your job {label} {reason}."
        return None

    def handle(self, event: JobEvent) -> None:
        user = self.store.get_user(event.job.username)
        if user is None or not user.email:
            return
        composed = self._compose(event)
        if composed is None:
            return
        kind, subject, text = composed
        if not user.notifications.get(kind, False):
            return
        if self.public_url:
            text += f"\n\nDashboard: {self.public_url}/dash"
        self.dispatcher.submit(Message(user.email, f"[{self.node_name}] {subject}", text, str(uuid4())))
