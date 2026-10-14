import os
import sys


def drop_privileges(user: str, data_dir: str) -> None:
    import pwd

    pw = pwd.getpwnam(user)
    os.makedirs(data_dir, exist_ok=True)
    for root, dirs, files in os.walk(data_dir, followlinks=False):
        for name in [root] + [os.path.join(root, f) for f in files + dirs]:
            if os.path.islink(name):
                continue
            try:
                os.chown(name, pw.pw_uid, pw.pw_gid, follow_symlinks=False)
            except OSError:
                pass
    os.setgroups([])
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ["HOME"] = pw.pw_dir
    os.environ["USER"] = user


def load_dotenv(path: str = ".env", env=None) -> list[str]:
    env = os.environ if env is None else env
    loaded = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in env:
            env[key] = value
            loaded.append(key)
    return loaded


def uvicorn_args(env: dict) -> list[str]:
    return [
        sys.executable, "-m", "uvicorn", "app.asgi:app",
        "--host", env.get("HOST", "0.0.0.0"),
        "--port", str(env.get("PORT", "8000")),
        "--proxy-headers",
        "--forwarded-allow-ips", env.get("FORWARDED_ALLOW_IPS", "*"),
        "--workers", "1",
        "--timeout-graceful-shutdown", "5",
        "--no-server-header",
    ]


def main() -> None:
    load_dotenv()
    db_path = os.environ.setdefault("DB_PATH", "./data/games.db")
    run_as = os.environ.get("RUN_AS_USER", "app")
    if os.name == "posix" and os.geteuid() == 0 and run_as:
        drop_privileges(run_as, os.path.dirname(os.path.abspath(db_path)))
    args = uvicorn_args(os.environ)
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
