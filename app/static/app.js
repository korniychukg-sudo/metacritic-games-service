(function () {
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  var TZ = (document.body && document.body.dataset.tz) || undefined;
  var STATUS = {
    idle: "ожидает", running: "работает", waiting: "ждёт запуска", ok: "в норме", error: "ошибка",
    off: "выключен", partial: "частично", failed: "сбой", empty: "нечего обрабатывать", interrupted: "прерван",
    cut: "остановлен по времени", skipped: "пропускается"
  };
  var TRIGGER = { manual: "вручную", schedule: "по расписанию", startup: "при старте" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmt(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d)) return String(iso);
    try {
      return new Intl.DateTimeFormat("ru-RU", {
        timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
      }).format(d).replace(",", "");
    } catch (e) {
      return d.toLocaleString("ru-RU");
    }
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function wait(seconds) {
    seconds = Math.max(0, Math.ceil(seconds));
    var h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  function secs(seconds) {
    if (seconds == null) return "идёт";
    if (seconds < 60) return seconds + " с";
    var m = Math.floor(seconds / 60);
    return m < 60 ? m + " мин " + pad(seconds % 60) + " с" : Math.floor(m / 60) + " ч " + pad(m % 60) + " мин";
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function setProgress(pct) {
    var bar = document.getElementById("progress-bar");
    if (bar) bar.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  }

  var manual = null;
  var pending = false;
  var wasRunning = null;

  function renderButton() {
    var btn = document.getElementById("run-btn");
    var hint = document.getElementById("run-hint");
    if (!btn || pending || !manual) return;
    var text = "Запустить обработку сейчас";
    var disabled = false;
    var hintText = "Ручных запусков сегодня: " + manual.runs_today + (manual.daily_cap ? " из " + manual.daily_cap : "") +
      ". После каждого запуска пауза " + Math.round(manual.cooldown_seconds / 60) + " мин.";
    if (manual.code === "busy") {
      text = "Обработка идёт…";
      disabled = true;
    } else if (manual.code === "cooldown" || manual.code === "cap") {
      var left = manual.available_at ? (new Date(manual.available_at) - Date.now()) / 1000 : manual.retry_after;
      if (left > 0) {
        disabled = true;
        if (manual.code === "cap") {
          text = "Лимит ручных запусков на сегодня";
          hintText = manual.reason || hintText;
        } else {
          text = "Запуск через " + wait(left);
          hintText = "Можно запустить через " + wait(left) + "." + (manual.reason ? " " + manual.reason + "." : "");
        }
      }
    }
    btn.disabled = disabled;
    btn.textContent = text;
    if (hint) hint.textContent = hintText;
  }

  function render(st) {
    var p = st.progress || {};
    var running = !!st.running;
    if (wasRunning && !running) {
      var flashEl = document.getElementById("flash");
      if (flashEl) flashEl.hidden = true;
    }
    wasRunning = running;
    document.getElementById("run-dot").className = "dot " + (running ? "running" : "idle");
    setText("run-state", running ? "Идёт обработка" : "Ожидание");
    setText("run-step", p.step ? p.step + (p.current && p.step.indexOf(p.current) < 0 ? ": " + p.current : "") : "");
    setProgress(p.total ? Math.round(100 * p.done / p.total) : 0);
    setText("progress-text", p.total
      ? "Запуск #" + p.run_id + ": " + p.done + " из " + p.total + " · успешно " + p.ok + ", ошибок " + p.failed
      : "Нет активной обработки");

    manual = st.manual || manual;
    renderButton();

    var t = st.totals || {};
    setText("t-total", t.games_total);
    setText("t-today", t.processed_today);
    setText("t-today-sub", "успешно " + t.processed_today_ok + " · " + st.day);
    setText("t-errors", t.errors_today);
    setText("t-errors-sub", "всего " + t.errors_total);
    var last = st.last_run;
    setText("t-last", last ? fmt(last.started_at) : "—");
    setText("t-last-sub", last
      ? (STATUS[last.status] || last.status) + " · " + last.ok + "/" + last.selected + " · " + (last.source_label || "—")
      : "запусков ещё не было");
    var cfg = st.config || {};
    setText("t-next", cfg.scheduler_enabled === false ? "вручную" : fmt(st.next_run_at));
    var ds = st.day_state || {};
    var pageSize = cfg.browse_page_size || 24;
    setText("t-cursor", ds.new_releases_done
      ? "New Releases пройден, дальше SEE ALL со страницы " + (Math.floor((ds.browse_offset || 0) / pageSize) + 1)
      : (st.runs_today ? "сегодня ещё не пройден блок New Releases" : "сегодня первым будет обработан блок New Releases"));

    (st.workers || []).forEach(function (w) {
      var card = document.querySelector('[data-worker="' + w.name + '"]');
      if (!card) return;
      card.querySelector(".dot").className = "dot " + w.status;
      card.querySelector(".st").textContent = STATUS[w.status] || w.status;
      card.querySelector(".detail").textContent = w.detail || "—";
    });

    var runs = document.getElementById("runs");
    if (runs) {
      runs.innerHTML = (st.runs || []).length ? st.runs.map(function (r) {
        return '<tr title="' + esc(r.message) + '"><td>' + esc(r.id) + "</td><td>" + esc(fmt(r.started_at)) + "</td><td>" +
          esc(r.trigger_label || TRIGGER[r.trigger] || r.trigger) + "</td><td>" + esc(r.source_label || "—") +
          '</td><td><span class="dot inline ' + esc(r.status) + '"></span> ' + esc(STATUS[r.status] || r.status) +
          (r.message ? '<div class="muted small run-msg">' + esc(r.message) + "</div>" : "") +
          "</td><td>" + esc(r.ok) + "/" + esc(r.selected) + (r.failed ? ", ошибок " + esc(r.failed) : "") +
          "</td><td>" + esc(secs(r.seconds)) + "</td></tr>";
      }).join("") : '<tr><td colspan="7" class="muted">Запусков ещё не было</td></tr>';
    }
    var errors = document.getElementById("errors");
    if (errors) {
      errors.innerHTML = (st.errors || []).length ? st.errors.map(function (e) {
        return "<tr><td>" + esc(fmt(e.at)) + "</td><td>" + esc(e.slug || "—") + '<br><span class="muted small">' + esc(e.step_label || e.step) +
          '</span></td><td class="small">' + esc(e.message) + "</td></tr>";
      }).join("") : '<tr><td colspan="3" class="muted">Ошибок нет</td></tr>';
    }
  }

  function appendLogs(items) {
    var box = document.getElementById("log");
    if (!box) return;
    var stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    items.forEach(function (e) {
      var div = document.createElement("div");
      div.className = e.level;
      div.innerHTML = '<span class="ts">' + esc(e.ts) + '</span> <span class="lg">' + esc(e.logger) + "</span> " + esc(e.message);
      box.appendChild(div);
      box.dataset.last = e.id;
    });
    while (box.childNodes.length > 500) box.removeChild(box.firstChild);
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function flash(text, ok) {
    var el = document.getElementById("flash");
    if (!el) return;
    el.hidden = false;
    el.className = "banner" + (ok ? " ok" : "");
    el.textContent = text;
  }

  function initMonitor() {
    var form = document.getElementById("run-form");
    var btn = document.getElementById("run-btn");
    var bar = document.getElementById("progress-bar");
    if (bar) setProgress(Number(bar.dataset.pct || 0));
    if (form) {
      var d = form.dataset;
      wasRunning = d.running === "1";
      manual = {
        code: d.code || "ok",
        available_at: d.availableAt || null,
        runs_today: Number(d.runsToday || 0),
        daily_cap: Number(d.dailyCap || 0),
        cooldown_seconds: Number(d.cooldown || 0),
        retry_after: Number(d.retryAfter || 0),
        reason: d.reason || ""
      };
    }
    if (form && window.fetch) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        pending = true;
        btn.disabled = true;
        btn.textContent = "Запускаю…";
        fetch("/run", { method: "POST", headers: { "Accept": "application/json" }, credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (body) { return { status: r.status, body: body }; }); })
          .then(function (res) {
            flash(res.status === 202 ? "Обработка запущена." : (res.body.detail || "Запуск отклонён."), res.status === 202);
          }).catch(function () {
            flash("Не удалось связаться с сервером.", false);
          }).finally(function () {
            pending = false;
            renderButton();
          });
      });
    }
    setInterval(renderButton, 1000);

    var connDot = document.getElementById("conn-dot");
    var connText = document.getElementById("conn-text");
    if (!window.EventSource) {
      connText.textContent = "браузер не поддерживает обновления в реальном времени";
      return;
    }
    var lastLog = 0;
    var es;
    function connect() {
      es = new EventSource("/api/events?last_log=" + lastLog);
      es.addEventListener("open", function () {
        connDot.className = "dot ok";
        connText.textContent = "обновляется в реальном времени";
      });
      es.addEventListener("status", function (ev) { render(JSON.parse(ev.data)); });
      es.addEventListener("log", function (ev) {
        var items = JSON.parse(ev.data);
        if (items.length) lastLog = items[items.length - 1].id;
        appendLogs(items);
      });
      es.addEventListener("error", function () {
        connDot.className = "dot error";
        connText.textContent = "соединение потеряно, переподключаюсь…";
        es.close();
        setTimeout(connect, 3000);
      });
    }
    connect();
  }

  if (document.getElementById("run-form")) initMonitor();
})();
