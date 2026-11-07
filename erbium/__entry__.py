from argparse import ArgumentParser, Namespace
from getpass import getpass
from re import fullmatch
from sys import exit as sys_exit, stdin

from erbium.api import create_docker_compose, command_to_start_docker_compose, run_command, command_to_initialize_docker
from erbium.api.docker.docker_compose import __DEFAULT_BASE_IMAGE__, __DEFAULT_SHARED_NETWORK__
from erbium.server.auth import EMAIL_PATTERN, MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, USERNAME_PATTERN, \
    hash_password
from erbium.server.run import DEFAULT_STATE_DIR, open_store, run_server


def _read_password(args: Namespace) -> str:
    if args.password_stdin:
        password = stdin.readline().rstrip("\r\n")
    else:
        password = getpass("Password: ")
        if getpass("Repeat password: ") != password:
            sys_exit("Passwords do not match")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        sys_exit(f"Passwords must be {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters long")
    return password


def _user_command(args: Namespace) -> None:
    store = open_store(args.state_dir)
    try:
        match args.user_cmd:
            case "add":
                if not fullmatch(USERNAME_PATTERN, args.username):
                    sys_exit("Usernames must be 2 to 32 characters of a-z, 0-9, '_', '.', '-', starting with a letter or digit")
                if args.email is not None and not fullmatch(EMAIL_PATTERN, args.email):
                    sys_exit("Invalid email address")
                store.create_user(args.username, hash_password(_read_password(args)), email=args.email,
                                  is_admin=args.admin)
                print(f"Created {'administrator' if args.admin else 'user'} {args.username}")
            case "passwd":
                if not store.set_password_hash(args.username, hash_password(_read_password(args))):
                    sys_exit(f"No user named {args.username}")
                print(f"Password changed for {args.username}; existing sessions were signed out")
            case "remove":
                if not store.delete_user(args.username):
                    sys_exit(f"No user named {args.username}")
                print(f"Removed {args.username}")
            case "list":
                for user in store.list_users():
                    print(f"{user.username}\t{'admin' if user.is_admin else 'user'}\t{user.email or '-'}")
    except ValueError as error:
        sys_exit(str(error))


def __entry__() -> None:
    parser = ArgumentParser(prog="Erbium", description="Erbium Compute Platform",
                            epilog="GitHub: https://github.com/ProjectNeura/Erbium")
    subparsers = parser.add_subparsers(dest="system", required=True)
    docker_parser = subparsers.add_parser("docker")
    docker_sub = docker_parser.add_subparsers(dest="docker_cmd", required=True)
    docker_init = docker_sub.add_parser("init")
    docker_init.add_argument("-n", "--shared_network", default=__DEFAULT_SHARED_NETWORK__)
    docker_init.add_argument("--protocol", choices=["quic", "http2"], default="quic")
    docker_create = docker_sub.add_parser("create")
    docker_create.add_argument("-n", "--service_name", required=True)
    docker_create.add_argument("-b", "--base_image", default=__DEFAULT_BASE_IMAGE__)
    docker_create.add_argument("--gpus", nargs="+", default=["all"], help="List of GPU IDs to use, or \"all\" for all available GPUs")
    docker_create.add_argument("--no-backup", action="store_true", help="Disable automatic Borg backups for the output directory")
    docker_create.add_argument("--env_file", default=None,
                               help="File with server secrets such as RESEND_API_KEY, passed to the container")
    docker_create.add_argument("--host_site_packages", default=None,
                               help="Host site-packages directory to mount read-only at the same path, so the container "
                                    "uses the host Python environment instead of a virtual environment")
    docker_create.add_argument("--host_scripts", default=None,
                               help="Host directory with the console scripts of those packages, such as /usr/local/bin")
    docker_create.add_argument("paths", nargs="+",
                               help="INPUT_DIR OUTPUT_DIR BACKUP_DIR SAVE_AS, or INPUT_DIR OUTPUT_DIR SAVE_AS with --no-backup")
    docker_run = docker_sub.add_parser("run")
    docker_run.add_argument("profile_path")
    docker_run.add_argument("service_name")
    docker_run.add_argument("-f", "--force_build", action="store_true")
    server_parser = subparsers.add_parser("server")
    server_sub = server_parser.add_subparsers(dest="server_cmd", required=True)
    server_run = server_sub.add_parser("run")
    server_run.add_argument("-p", "--port", type=int, default=8000)
    server_run.add_argument("--host", default="0.0.0.0")
    server_run.add_argument("--state_dir", default=DEFAULT_STATE_DIR)
    server_run.add_argument("--jupyter_executable", default="/workspace/venv/bin/jupyter")
    server_run.add_argument("--max_gpu_utilization", type=float, default=15, help="Percent")
    server_run.add_argument("--max_gpu_memory_utilization", type=float, default=15, help="Percent")
    server_run.add_argument("--log_level", default="warning", choices=["critical", "error", "warning", "info", "debug", "trace"])
    server_run.add_argument("--access_log", action="store_true")
    user_parser = subparsers.add_parser("user")
    user_sub = user_parser.add_subparsers(dest="user_cmd", required=True)
    for command in ("add", "passwd", "remove", "list"):
        user_command = user_sub.add_parser(command)
        user_command.add_argument("--state_dir", default=DEFAULT_STATE_DIR)
        if command == "list":
            continue
        user_command.add_argument("username")
        if command == "add":
            user_command.add_argument("--email", default=None)
            user_command.add_argument("--admin", action="store_true")
        if command in ("add", "passwd"):
            user_command.add_argument("--password_stdin", action="store_true",
                                      help="Read the password from the first line of standard input")
    args = parser.parse_args()
    match args.system:
        case "docker":
            match args.docker_cmd:
                case "init":
                    with open("cloudflared_tunnel_token.txt") as f:
                        run_command(command_to_initialize_docker(f.read().strip(), protocol=args.protocol,
                                                                 shared_network=args.shared_network))
                case "create":
                    expected_path_count = 3 if args.no_backup else 4
                    if len(args.paths) != expected_path_count:
                        docker_create.error(
                            f"expected {expected_path_count} path arguments when "
                            f"{'--no-backup is set' if args.no_backup else '--no-backup is not set'}"
                        )
                    if args.no_backup:
                        input_dir, output_dir, save_as = args.paths
                        backup_dir = None
                    else:
                        input_dir, output_dir, backup_dir, save_as = args.paths
                    with open(save_as, "w") as f:
                        f.write(create_docker_compose(
                            args.service_name, base_image=args.base_image, hostname=args.service_name,
                            container_name=args.service_name, input_dir=input_dir, output_dir=output_dir,
                            backup_dir=backup_dir, gpus=args.gpus, env_file=args.env_file,
                            host_site_packages=args.host_site_packages, host_scripts=args.host_scripts
                        ))
                case "run":
                    run_command(command_to_start_docker_compose(
                        args.profile_path, args.service_name, force_build=args.force_build
                    ))
        case "server":
            match args.server_cmd:
                case "run":
                    run_server(args.port, host=args.host, state_dir=args.state_dir, jupyter_executable=args.jupyter_executable,
                               max_gpu_utilization=args.max_gpu_utilization,
                               max_gpu_memory_utilization=args.max_gpu_memory_utilization,
                               log_level=args.log_level, access_log=args.access_log)
        case "user":
            _user_command(args)
