import threading
from datetime import timedelta

from app.monitor import Monitor
from app.pipeline import AlreadyRunning
from app.scheduler import Scheduler, initial_next_run, interval_of

from .conftest import FakeClock


class FakePipeline:
    def __init__(self, db, clock, settings):
        self.db = db
        self.clock = clock
        self.settings = settings
        self.running = False
        self.runs = []
        self.fail_with: Exception | None = None
        self.duration = timedelta(minutes=10)

    def run(self, trigger="manual", limit=None):
        if self.fail_with:
            exc, self.fail_with = self.fail_with, None
            raise exc
        start = self.clock()
        run_id = self.db.create_run(trigger, self.settings.today(start), "test", start)
        self.clock.advance(seconds=self.duration.total_seconds())
        self.db.finish_run(run_id, "ok", 1, 1, 0, "new-releases", "", self.clock())
        self.runs.append((trigger, start))
        return {"run_id": run_id}


def make(settings, db, clock=None):
    clock = clock or FakeClock()
    settings.scheduler_enabled = True
    settings.startup_delay_seconds = 15
    settings.run_interval_minutes = 60
    pipe = FakePipeline(db, clock, settings)
    sched = Scheduler(settings, db, pipe, Monitor(), clock=clock)
    return sched, pipe, clock


def test_initial_next_run_after_restart(settings, db, clock):
    now = clock()
    assert initial_next_run(settings, db, now) == now + timedelta(seconds=settings.startup_delay_seconds)
    db.create_run("schedule", "d", "x", now - timedelta(minutes=20))
    assert initial_next_run(settings, db, now) == now + timedelta(minutes=40)
    db.create_run("schedule", "d", "x", now - timedelta(hours=3))
    with db.write() as c:
        c.execute("DELETE FROM runs WHERE started_at > ?", ((now - timedelta(hours=1)).isoformat(),))
    assert initial_next_run(settings, db, now) == now + timedelta(seconds=settings.startup_delay_seconds)


def test_db_with_games_but_no_runs_waits_a_full_interval(settings, db, clock):
    from .test_db import make_game

    db.upsert_game(make_game(), "x")
    assert initial_next_run(settings, db, clock()) == clock() + interval_of(settings)


def test_tick_waits_then_runs_and_schedules_from_the_run_start(settings, db):
    sched, pipe, clock = make(settings, db)
    assert sched.tick() == "wait"
    clock.advance(seconds=16)
    assert sched.tick() == "ran"
    first_start = pipe.runs[0][1]
    assert sched.next_run == first_start + timedelta(minutes=60)
    clock.advance(minutes=30)
    assert sched.tick() == "wait"
    clock.now = sched.next_run
    assert sched.tick() == "ran"
    assert len(pipe.runs) == 2 and pipe.runs[1][0] == "schedule"


def test_slot_is_not_dropped_while_another_run_is_active(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.tick()
    clock.advance(seconds=16)
    sched.tick()
    clock.now = sched.next_run
    pipe.running = True
    due = sched.next_run
    assert sched.tick() == "busy"
    clock.advance(minutes=1)
    assert sched.tick() == "busy"
    assert sched.next_run == due
    pipe.running = False
    clock.advance(minutes=1)
    assert sched.tick() == "ran"
    assert len(pipe.runs) == 2


def test_manual_run_counts_as_the_slot(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.tick()
    clock.advance(seconds=16)
    sched.tick()
    clock.advance(minutes=40)
    manual_start = clock()
    db.create_run("manual", settings.today(manual_start), "web", manual_start)
    assert sched.tick() == "wait"
    assert sched.next_run == manual_start + timedelta(minutes=60)


def test_another_process_taking_the_run_keeps_the_slot_due(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.tick()
    clock.advance(seconds=16)
    pipe.fail_with = AlreadyRunning()
    due = sched.next_run
    assert sched.tick() == "busy"
    assert sched.next_run == due


def test_crashing_run_does_not_stop_the_scheduler(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.tick()
    clock.advance(seconds=16)
    pipe.fail_with = RuntimeError("boom")
    assert sched.tick() == "crashed"
    assert "boom" in sched.last_error
    assert sched.next_run > clock()
    clock.now = sched.next_run
    assert sched.tick() == "ran"


def test_loop_survives_exceptions_in_tick(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.poll_seconds = 0.01
    sched.min_wait_seconds = 0.001
    calls = []
    done = threading.Event()

    def flaky_tick(now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        if len(calls) >= 3:
            sched._stop.set()
            done.set()
        sched.next_run = clock()
        return "wait"

    sched.tick = flaky_tick
    sched.start()
    assert done.wait(5)
    sched.stop()
    assert len(calls) >= 3
    assert sched.last_error is None


def test_stale_runs_from_a_dead_process_are_closed_on_tick(settings, db):
    sched, pipe, clock = make(settings, db)
    db.create_run("schedule", "d", "dead-process", clock() - timedelta(minutes=30))
    sched.tick()
    assert db.last_run()["status"] == "interrupted"


def test_interval_is_never_below_one_minute(settings):
    settings.run_interval_minutes = 0
    assert interval_of(settings) == timedelta(minutes=1)
    settings.run_interval_minutes = -10
    assert interval_of(settings) == timedelta(minutes=1)


def test_health_reports_dead_thread_and_overdue_schedule(settings, db):
    sched, pipe, clock = make(settings, db)
    assert sched.health()["healthy"] is False
    alive = threading.Event()
    sched._thread = threading.Thread(target=alive.wait, daemon=True)
    sched._thread.start()
    try:
        sched.tick()
        assert sched.health()["healthy"] is True
        clock.now = sched.next_run + timedelta(minutes=6)
        h = sched.health()
        assert h["healthy"] is False and h["overdue_seconds"] >= 360
        pipe.running = True
        assert sched.health()["healthy"] is True
    finally:
        alive.set()
    settings.scheduler_enabled = False
    assert Scheduler(settings, db, pipe, Monitor(), clock=clock).health()["healthy"] is True


def test_next_run_shown_during_a_run_is_the_upcoming_slot(settings, db):
    sched, pipe, clock = make(settings, db)
    sched.tick()
    clock.advance(seconds=16)
    seen = []
    original = pipe.run

    def spy(trigger="manual", limit=None):
        seen.append(sched.monitor.next_run_at)
        return original(trigger, limit)

    pipe.run = spy
    start = clock()
    sched.tick()
    assert seen == [(start + timedelta(minutes=60)).isoformat(timespec="seconds")]
