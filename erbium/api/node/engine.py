from dataclasses import dataclass
from logging import getLogger
from threading import Event, Thread
from time import time
from typing import Callable, Literal, Protocol, Sequence

from erbium.api.node.store import EndReason, Job, Store
from erbium.api.os import GPUInfo, generate_secret

logger = getLogger(__name__)

EventKind = Literal["queued", "started", "ending", "finished"]


@dataclass(frozen=True)
class JobEvent(object):
    kind: EventKind
    job: Job


class Account(Protocol):
    def grant(self, ssh_password: str, public_keys: Sequence[str], jupyter_token: str) -> None:
        ...

    def set_public_keys(self, public_keys: Sequence[str]) -> None:
        ...

    def revoke(self) -> None:
        ...


class Node(object):
    def __init__(self, store: Store, account: Account, *, max_gpu_utilization: float = 15,
                 max_gpu_memory_utilization: float = 15, on_event: Callable[[JobEvent], None] | None = None,
                 interval: float = 1) -> None:
        self.store: Store = store
        self.max_gpu_utilization: float = max_gpu_utilization
        self.max_gpu_memory_utilization: float = max_gpu_memory_utilization
        self._account: Account = account
        self._on_event: Callable[[JobEvent], None] | None = on_event
        self._interval: float = interval
        self._active_job_id: int | None = None
        self._granted: bool = False
        self._unannounced_job_id: int | None = None
        self._keys_changed: Event = Event()
        self._stopping: Event = Event()
        self._thread: Thread = Thread(target=self._run, daemon=True, name="Node")

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join()

    def _emit(self, kind: EventKind, job: Job) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(JobEvent(kind, job))
        except Exception:
            logger.exception("Failed to handle %s event for job %d", kind, job.id)

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.lock()
                break
            except Exception:
                logger.exception("Failed to lock the account at startup")
                self._stopping.wait(self._interval)
        while not self._stopping.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            self._stopping.wait(self._interval)

    def _public_keys(self, username: str) -> tuple[str, ...]:
        user = self.store.get_user(username)
        return () if user is None else user.public_keys

    def _grant(self, job: Job) -> None:
        assert job.ssh_password is not None and job.jupyter_token is not None
        self._active_job_id = job.id
        self._granted = False
        self._keys_changed.clear()
        self._account.grant(job.ssh_password, self._public_keys(job.username), job.jupyter_token)
        self._granted = True
        if self._unannounced_job_id == job.id:
            self._unannounced_job_id = None
            self._emit("started", job)

    def _release(self) -> None:
        job_id = self._active_job_id
        self._account.revoke()
        self._active_job_id = None
        self._granted = False
        ended = None if job_id is None else self.store.get_job(job_id)
        if ended is not None and ended.status != "running":
            self._emit("finished", ended)

    def tick(self, now: float | None = None) -> None:
        now = time() if now is None else now
        running = self.store.running_job()
        if self._active_job_id is not None and (running is None or running.id != self._active_job_id):
            self._release()
        if running is not None:
            ends_at = running.ends_at()
            assert ends_at is not None
            if now >= ends_at:
                self.store.finish_job(running.id, "expired")
                self._active_job_id = running.id
                self._release()
                running = None
            else:
                if not self._granted:
                    self._grant(running)
                elif self._keys_changed.is_set():
                    self._keys_changed.clear()
                    self._account.set_public_keys(self._public_keys(running.username))
                notice = 60 * self.store.get_settings().ending_notice_minutes
                if (not running.ending_notified and 3600 * running.requested_run_time_hrs > notice
                        and ends_at - now <= notice and self.store.mark_ending_notified(running.id)):
                    self._emit("ending", running)
        if running is None:
            started = self.store.start_next(generate_secret(), generate_secret(48), now)
            if started is not None:
                self._unannounced_job_id = started.id
                self._grant(started)

    def lock(self) -> None:
        if self.store.running_job() is None:
            self._account.revoke()

    def public_keys_changed(self, username: str) -> None:
        running = self.store.running_job()
        if running is not None and running.username == username:
            self._keys_changed.set()

    def join(self, username: str, name: str, requested_run_time_hrs: float) -> Job:
        job = self.store.enqueue(username, name, requested_run_time_hrs)
        self._emit("queued", job)
        return job

    def leave(self, job_id: int, username: str) -> Job | None:
        return self.store.cancel_job(job_id, "left", username=username)

    def stop(self, job_id: int, *, username: str | None = None) -> Job | None:
        reason: EndReason = "admin_stopped" if username is None else "stopped"
        return self.store.finish_job(job_id, reason, username=username)

    def remove(self, job_id: int) -> Job | None:
        job = self.store.cancel_job(job_id, "admin_removed")
        if job is not None:
            self._emit("finished", job)
        return job

    def schedule(self, now: float | None = None) -> list[tuple[Job, float]]:
        now = time() if now is None else now
        running = self.store.running_job()
        start = now
        if running is not None:
            ends_at = running.ends_at()
            assert ends_at is not None
            start = max(now, ends_at)
        plan: list[tuple[Job, float]] = []
        for job in self.store.queued_jobs():
            plan.append((job, start))
            start += 3600 * job.requested_run_time_hrs
        return plan

    def wait_time_hrs(self, now: float | None = None, *, plan: list[tuple[Job, float]] | None = None) -> float:
        now = time() if now is None else now
        plan = self.schedule(now) if plan is None else plan
        if not plan:
            running = self.store.running_job()
            ends_at = None if running is None else running.ends_at()
            return 0 if ends_at is None else max(0., ends_at - now) / 3600
        last, start = plan[-1]
        return (start + 3600 * last.requested_run_time_hrs - now) / 3600

    def is_available(self, device: GPUInfo) -> bool:
        utilization = device.utilization_percent
        return ((utilization is None or utilization < self.max_gpu_utilization)
                and device.memory_utilization_percent < self.max_gpu_memory_utilization)
