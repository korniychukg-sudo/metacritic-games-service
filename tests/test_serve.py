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
    assert "--reload" not in args
    assert serve.uvicorn_args({})[serve.uvicorn_args({}).index("--port") + 1] == "8000"


def test_main_sets_db_default_and_execs_without_root(monkeypatch):
    seen = {}
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
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "games.db"))
    monkeypatch.setenv("RUN_AS_USER", "app")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(serve, "drop_privileges", lambda user, data_dir: seen.update(user=user, data_dir=data_dir))
    monkeypatch.setattr(os, "execv", lambda path, args: seen.update(args=args))
    serve.main()
    assert seen["user"] == "app" and seen["data_dir"] == str(tmp_path / "data")
    assert "args" in seen
