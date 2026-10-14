import os
import sys

from app import serve


def test_uvicorn_args_take_port_from_env_and_trust_the_proxy():
    args = serve.uvicorn_args({"PORT": "4321"})
    assert args[:4] == [sys.executable, "-m", "uvicorn", "app.asgi:app"]
    assert args[args.index("--port") + 1] == "4321"
    assert args[args.index("--forwarded-allow-ips") + 1] == "*"
    assert "--proxy-headers" in args
    assert args[args.index("--workers") + 1] == "1"
    assert args[args.index("--timeout-graceful-shutdown") + 1] == "5"
    assert "--reload" not in args
    assert serve.uvicorn_args({})[serve.uvicorn_args({}).index("--port") + 1] == "8000"


def test_main_sets_db_default_and_execs_without_root(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("PORT", "9100")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(serve, "drop_privileges", lambda *a: seen.setdefault("dropped", a))
    monkeypatch.setattr(os, "execv", lambda path, args: seen.update(path=path, args=args))
    serve.main()
    assert os.environ["DB_PATH"] == "./data/games.db"
    assert "dropped" not in seen
    assert seen["args"][seen["args"].index("--port") + 1] == "9100"


def test_main_drops_root_to_the_app_user(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "games.db"))
    monkeypatch.setenv("RUN_AS_USER", "app")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(serve, "drop_privileges", lambda user, data_dir: seen.update(user=user, data_dir=data_dir))
    monkeypatch.setattr(os, "execv", lambda path, args: seen.update(args=args))
    serve.main()
    assert seen["user"] == "app" and seen["data_dir"] == str(tmp_path / "data")
    assert "args" in seen


def test_privilege_drop_never_follows_symlinks(monkeypatch, tmp_path):
    import pwd
    from types import SimpleNamespace

    data = tmp_path / "data"
    (data / "models").mkdir(parents=True)
    (data / "games.db").write_text("x")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (data / "evil").symlink_to(outside)
    (data / "evil-dir").symlink_to(tmp_path)
    calls = []
    monkeypatch.setattr(pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=10001, pw_gid=10001, pw_dir="/app"))
    monkeypatch.setattr(os, "chown", lambda path, uid, gid, follow_symlinks=True: calls.append((path, follow_symlinks)))
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(os, name, lambda *a: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    serve.drop_privileges("app", str(data))
    paths = {os.path.basename(p) for p, _ in calls}
    assert paths == {"data", "models", "games.db"}
    assert all(follow is False for _, follow in calls)


def test_dotenv_fills_only_missing_and_non_empty_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nBATCH_SIZE=4\nexport DAY_TZ='Europe/Berlin'\nRUN_TOKEN=\nPORT=1\nbroken line\n")
    env = {"PORT": "9000"}
    loaded = serve.load_dotenv(str(env_file), env)
    assert env == {"PORT": "9000", "BATCH_SIZE": "4", "DAY_TZ": "Europe/Berlin"}
    assert loaded == ["BATCH_SIZE", "DAY_TZ"]
    assert serve.load_dotenv(str(tmp_path / "missing"), {}) == []
