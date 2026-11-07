from dataclasses import dataclass
from logging import getLogger
from os import O_CREAT, O_TRUNC, O_WRONLY, PathLike, chmod, close, open as open_descriptor, replace
from pathlib import Path
from re import fullmatch
from secrets import choice
from string import ascii_letters, digits
from subprocess import DEVNULL, STDOUT, Popen, TimeoutExpired, run
from time import monotonic, sleep
from typing import Sequence

logger = getLogger(__name__)

SECRET_ALPHABET: str = ascii_letters + digits


def generate_secret(length: int = 24) -> str:
    return "".join(choice(SECRET_ALPHABET) for _ in range(length))


@dataclass(frozen=True)
class Identity(object):
    username: str
    uid: int
    gid: int
    groups: tuple[int, ...]
    home: str
    shell: str


def lookup_identity(username: str) -> Identity:
    from os import getgrouplist
    from pwd import getpwnam

    entry = getpwnam(username)
    return Identity(username, entry.pw_uid, entry.pw_gid, tuple(getgrouplist(username, entry.pw_gid)), entry.pw_dir,
                    entry.pw_shell or "/bin/bash")


class LinuxAccount(object):
    CLI_LOGOUT_SCRIPT: str = (
        "if command -v codex >/dev/null 2>&1; then codex logout || true; fi; "
        "if command -v claude >/dev/null 2>&1; then claude auth logout || claude logout || true; fi; "
        "if command -v gh >/dev/null 2>&1; then gh auth logout --hostname github.com || true; fi; "
        "true"
    )
    SYSTEM_PATH: str = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    def __init__(self, username: str = "access", *, authorized_keys_dir: str | PathLike[str] = "/etc/erbium/authorized_keys",
                 jupyter_executable: str = "/workspace/venv/bin/jupyter", jupyter_root: str = "/workspace",
                 jupyter_port: int = 8080, jupyter_log: str | PathLike[str] = "/var/log/erbium-jupyter.log") -> None:
        self.username: str = username
        self.authorized_keys_dir: Path = Path(authorized_keys_dir)
        self.jupyter_executable: str = jupyter_executable
        self.jupyter_root: str = jupyter_root
        self.jupyter_port: int = jupyter_port
        self.jupyter_log: Path = Path(jupyter_log)
        self._jupyter: Popen[bytes] | None = None

    def _environment(self, identity: Identity) -> dict[str, str]:
        return {
            "HOME": identity.home, "USER": identity.username, "LOGNAME": identity.username, "SHELL": identity.shell,
            "LANG": "C.UTF-8",
            "PATH": f"{identity.home}/.local/bin:/workspace/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        }

    def _set_password(self, password: str) -> None:
        if not fullmatch(r"[A-Za-z0-9]{16,}", password):
            raise ValueError("Generated SSH passwords must be at least 16 alphanumeric characters")
        run(("chpasswd",), input=f"{self.username}:{password}\n", text=True, check=True, capture_output=True)

    def set_public_keys(self, public_keys: Sequence[str]) -> None:
        self.authorized_keys_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        target = self.authorized_keys_dir / self.username
        staging = target.with_name(f".{self.username}.tmp")
        staging.write_text("".join(f"{key}\n" for key in public_keys))
        chmod(staging, 0o644)
        replace(staging, target)

    def _start_jupyter(self, token: str) -> None:
        identity = lookup_identity(self.username)
        environment = self._environment(identity)
        environment["JUPYTER_TOKEN"] = token
        descriptor = open_descriptor(self.jupyter_log, O_WRONLY | O_CREAT | O_TRUNC, 0o600)
        chmod(self.jupyter_log, 0o600)
        with open(descriptor, "wb") as log:
            self._jupyter = Popen(
                (self.jupyter_executable, "lab", "--no-browser", f"--port={self.jupyter_port}", "--ip=0.0.0.0",
                 "--ServerApp.port_retries=0", f"--ServerApp.root_dir={self.jupyter_root}",
                 "--ServerApp.trust_xheaders=True", "--ServerApp.allow_remote_access=True"),
                stdin=DEVNULL, stdout=log, stderr=STDOUT, cwd=identity.home, env=environment, user=identity.uid,
                group=identity.gid, extra_groups=list(identity.groups), start_new_session=True
            )

    def _reap_jupyter(self) -> None:
        if self._jupyter is None:
            return
        try:
            self._jupyter.wait(timeout=10)
        except TimeoutExpired:
            logger.warning("Jupyter did not exit after its session was killed")
        self._jupyter = None

    def _logout_cli(self) -> None:
        identity = lookup_identity(self.username)
        try:
            run(("/bin/bash", "--noprofile", "--norc", "-c", self.CLI_LOGOUT_SCRIPT), stdin=DEVNULL, stdout=DEVNULL,
                stderr=DEVNULL, timeout=60, cwd=identity.home,
                env={**self._environment(identity), "PATH": self.SYSTEM_PATH}, user=identity.uid, group=identity.gid,
                extra_groups=list(identity.groups), check=False)
        except TimeoutExpired:
            logger.warning("CLI logout timed out")

    def grant(self, ssh_password: str, public_keys: Sequence[str], jupyter_token: str) -> None:
        self._set_password(ssh_password)
        self.set_public_keys(public_keys)
        self._start_jupyter(jupyter_token)

    def _remaining(self) -> bool:
        if self._jupyter is not None:
            self._jupyter.poll()
        return run(("pgrep", "-u", self.username), stdout=DEVNULL, stderr=DEVNULL, check=False).returncode == 0

    def _kill_everything(self, timeout: float = 30) -> None:
        identity = lookup_identity(self.username)
        deadline = monotonic() + timeout
        while True:
            run(("/bin/kill", "-KILL", "-1"), stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, env={},
                user=identity.uid, group=identity.gid, extra_groups=[], check=False)
            if not self._remaining():
                return
            if monotonic() >= deadline:
                logger.error("Processes of %s survived for %g seconds after SIGKILL", self.username, timeout)
                return
            sleep(.2)

    def revoke(self) -> None:
        self._set_password(generate_secret(48))
        self.set_public_keys(())
        self._kill_everything()
        self._reap_jupyter()
        self._logout_cli()
        self._kill_everything()
