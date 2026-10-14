(function () {
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  var STATUS = {
    idle: "ожидает", running: "работает", waiting: "ждёт запуска", ok: "в норме", error: "ошибка",
    off: "выключен", partial: "частично", failed: "сбой", empty: "нечего обрабатывать", interrupted: "прерван"
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
    if (isNaN(d)) return iso;
    try {
      return new Intl.DateTimeFormat("ru-RU", {
        timeZone: window.APP_TZ || undefined, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
      }).format(d).replace(",", "");
    } catch (e) {
      return d.toLocaleString("ru-RU");
    }
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function render(st) {
    var p = st.progress || {};
    var running = !!st.running;
    document.getElementById("run-dot").className = "dot " + (running ? "running" : "idle");
    setText("run-state", running ? "Идёт обработка" : "Ожидание");
    setText("run-step", p.step ? p.step + (p.current && p.step.indexOf(p.current) < 0 ? ": " + p.current : "") : "");
    var pct = p.total ? Math.round(100 * p.done / p.total) : 0;
    document.getElementById("progress-bar").style.width = pct + "%";
    setText("progress-text", p.total
      ? "Запуск #" + p.run_id + ": " + p.done + " из " + p.total + " · успешно " + p.ok + ", ошибок " + p.failed
      : "Нет активной обработки");

    var btn = document.getElementById("run-btn");
    if (btn && !btn.dataset.pending) {
      btn.disabled = running;
      btn.textContent = running ? "Обработка идёт…" : "Запустить обработку сейчас";
    }

    var t = st.totals || {};
    setText("t-total", t.games_total);
    setText("t-today", t.processed_today);
    setText("t-today-sub", "успешно " + t.processed_today_ok + " · " + st.day);
    setText("t-errors", t.errors_today);
    setText("t-errors-sub", "всего " + t.errors_total);
    var last = st.last_run;
    setText("t-last", last ? fmt(last.started_at) : "—");
    setText("t-last-sub", last ? (STATUS[last.status] || last.status) + " · " + last.ok + "/" + last.selected : "");
    setText("t-next", fmt(st.next_run_at));
    var ds = st.day_state || {};
    setText("t-cursor", ds.new_releases_done
      ? "New Releases пройден, SEE ALL с позиции " + ds.browse_offset
      : "сегодня ещё идёт блок New Releases");

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
        return "<tr><td>" + r.id + "</td><td>" + fmt(r.started_at) + "</td><td>" + esc(TRIGGER[r.trigger] || r.trigger) +
          '</td><td><span class="dot ' + esc(r.status) + '" style="display:inline-block"></span> ' + esc(STATUS[r.status] || r.status) +
          "</td><td>" + r.ok + "/" + r.selected + (r.failed ? ", ошибок " + r.failed : "") + "</td></tr>";
      }).join("") : '<tr><td colspan="5" class="muted">Запусков ещё не было</td></tr>';
    }
    var errors = document.getElementById("errors");
    if (errors) {
      errors.innerHTML = (st.errors || []).length ? st.errors.map(function (e) {
        return "<tr><td>" + fmt(e.at) + "</td><td>" + esc(e.slug || "—") + '<br><span class="muted small">' + esc(e.step) +
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

  window.initMonitor = function () {
    var form = document.getElementById("run-form");
    var btn = document.getElementById("run-btn");
    if (form && window.fetch) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        btn.disabled = true;
        btn.dataset.pending = "1";
        btn.textContent = "Запускаю…";
        fetch("/api/run", { method: "POST" }).then(function (r) {
          flash(r.status === 202 ? "Обработка запущена." : "Обработка уже идёт, второй запуск не нужен.", r.status === 202);
        }).catch(function () {
          flash("Не удалось связаться с сервером.", false);
        }).finally(function () {
          delete btn.dataset.pending;
        });
      });
    }

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
  };
})();
