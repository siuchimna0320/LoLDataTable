/* 積分榜客戶端渲染：篩選／排序／我的最愛皆在瀏覽器完成，
   整張表以單次 innerHTML 輸出，避免 Dash 大型元件樹效能瓶頸。 */
(function () {
  "use strict";

  var LS_KEY = "dpm-fav-puuids";

  var state = {
    rows: [],
    daily: { bounds: null, teams: [] },
    tab: "ladder",      // ladder | daily
    days: "y",          // y | d3 | d7（每日戰況日期窗）
    favs: {},
    filter: { pos: "all", region: "all" },
    sort: { key: "lp", dir: "desc" },
  };

  var STAGE_ZH = {
    leaderboards: "排行榜", pros: "選手檔", matches: "對戰紀錄",
    details: "單場明細", done: "完成", failed: "失敗",
  };

  /* -------------------------------------------------------------- 工具 */
  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function loadFavs() {
    try {
      (JSON.parse(localStorage.getItem(LS_KEY)) || []).forEach(function (p) {
        state.favs[p] = true;
      });
    } catch (e) { state.favs = {}; }
  }

  function saveFavs() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(Object.keys(state.favs)));
    } catch (e) { /* 隱私模式等狀況下靜默失敗 */ }
  }

  function scoreColor(s) {
    if (s == null) return "#8a97ab";
    if (s >= 80) return "#ff6b57";
    if (s >= 65) return "#f0a93c";
    if (s >= 50) return "#cfc38a";
    return "#4caf7d";
  }

  function num(v) { return v == null ? -Infinity : Number(v); }

  /* -------------------------------------------------------------- 表格 */
  var HEADERS = [
    { t: "#", cls: "col-rank" },
    { t: "戰隊 選手", cls: "col-player" },
    { t: "路線", cls: "col-pos" },
    { t: "排名", cls: "col-tier" },
    { t: "分數", key: "lp" },
    { t: "場數(勝-敗)", key: "games" },
    { t: "勝率", key: "winrate" },
    { t: "一週評分", key: "wk_score" },
    { t: "一週KDA", key: "wk_kda" },
    { t: "最近十場", cls: "col-champs" },
    { t: "伺服器", cls: "col-server" },
    { t: "帳號", cls: "col-account" },
    { t: "最近積分", key: "last_match_ts" },
  ];

  function headerHtml() {
    return HEADERS.map(function (h) {
      var sortable = !!h.key;
      var arrow = "";
      if (sortable && state.sort.key === h.key) {
        arrow = state.sort.dir === "desc" ? " ▼" : " ▲";
      }
      var cls = "dpm-th" + (sortable ? " sortable" : "") +
        (h.cls ? " " + h.cls : "");
      var attr = sortable ? ' data-sort="' + h.key + '"' : "";
      return '<div class="' + cls + '"' + attr + ">" +
        esc(h.t) + '<span class="dpm-arrow">' + arrow + "</span></div>";
    }).join("");
  }

  function recentChampsHtml(row) {
    return (row.recent_champs || []).map(function (c) {
      var cls = "dpm-champ " + (c.win ? "is-win" : "is-loss");
      return '<img class="' + cls + '" src="/champ-img/' +
        encodeURIComponent(c.champ) + '.png" alt="' + esc(c.champ) +
        '" loading="lazy">';
    }).join("");
  }

  function playerHtml(row) {
    var fav = state.favs[row.puuid] ? "dpm-fav is-on" : "dpm-fav";
    var star = '<span class="' + fav + '" data-puuid="' + esc(row.puuid) +
      '" title="我的最愛（置頂）">' +
      (state.favs[row.puuid] ? "★" : "☆") + "</span>";
    var logo = row.team_logo
      ? '<img class="dpm-team-logo" src="' + esc(row.team_logo) +
        '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
      : "";
    var label = esc((row.team ? row.team + " " : "") +
      (row.display_name || row.game_name || ""));
    var name = row.display_name
      ? '<a class="dpm-player-link" data-dpm-nav href="/ladder/p/' +
        encodeURIComponent(row.display_name) + '">' + label + "</a>"
      : '<span class="dpm-player-link">' + label + "</span>";
    return star + logo + name;
  }

  function rowHtml(row, pinned) {
    var games = (row.wins || 0) + (row.losses || 0);
    var week = row.wk_score == null
      ? '<span class="dpm-muted">—</span>'
      : '<span class="dpm-cell-tip" style="color:' +
        scoreColor(row.wk_score) + '" data-tip="最近 7 天單雙排每場 ' +
        'DPM.LOL 評分的算術平均；括號為勝-敗場；一週 KDA 為同期 (K+A)/D">' +
        row.wk_score.toFixed(1) +
        ' <span class="dpm-wk-sub">(' + (row.wk_wins || 0) + "勝" +
        (row.wk_losses || 0) + "敗)</span></span>";
    var account = row.profile_url
      ? '<a class="dpm-acct-link" href="' + esc(row.profile_url) +
        '" target="_blank" rel="noopener noreferrer" title="於 dpm.lol 開啟">' +
        esc(row.game_name) + "#" + esc(row.tag_line) + "</a>"
      : "";
    var cells = [
      '<div class="dpm-td col-rank">' + row.__rank + "</div>",
      '<div class="dpm-td col-player">' + playerHtml(row) + "</div>",
      '<div class="dpm-td col-pos">' +
        (row.pos ? '<img class="dpm-pos-icon" src="/pos-img/' + row.pos +
          '.svg" alt="' + row.pos + '">' : "") + "</div>",
      '<div class="dpm-td col-tier"><img class="dpm-rank-icon" src="' +
        esc(row.rank_icon) + '" alt=""><span style="color:' +
        esc(row.tier_color) + '">' + esc(row.tier_zh) + "</span></div>",
      '<div class="dpm-td">' + num(row.lp) + "</div>",
      '<div class="dpm-td">' + games + " <span class='dpm-muted'>(" +
        (row.wins || 0) + "-" + (row.losses || 0) + ")</span></div>",
      '<div class="dpm-td">' +
        (row.winrate == null ? "—" : row.winrate.toFixed(0) + "%") + "</div>",
      '<div class="dpm-td">' + week + "</div>",
      '<div class="dpm-td">' +
        (row.wk_kda == null ? '<span class="dpm-muted">—</span>'
          : row.wk_kda.toFixed(1)) + "</div>",
      '<div class="dpm-td col-champs"><div class="dpm-champs">' +
        recentChampsHtml(row) + "</div></div>",
      '<div class="dpm-td col-server">' + esc(row.server) + "</div>",
      '<div class="dpm-td col-account">' + account + "</div>",
      '<div class="dpm-td">' + esc(row.last_date || "—") + "</div>",
    ];
    return '<div class="dpm-row' + (pinned ? " is-pinned" : "") +
      '">' + cells.join("") + "</div>";
  }

  function filteredSorted() {
    var rows = state.rows.filter(function (r) {
      if (state.filter.pos !== "all" && r.pos !== state.filter.pos)
        return false;
      if (state.filter.region !== "all" &&
          r.region !== state.filter.region)
        return false;
      return true;
    });
    var key = state.sort.key, dir = state.sort.dir === "desc" ? -1 : 1;
    rows.sort(function (a, b) {
      var va = key === "games"
        ? (a.wins || 0) + (a.losses || 0)
        : a[key];
      var vb = key === "games"
        ? (b.wins || 0) + (b.losses || 0)
        : b[key];
      va = num(va); vb = num(vb);
      if (va !== vb) return (va - vb) * dir;
      return 0;
    });
    rows.forEach(function (r, i) { r.__rank = i + 1; });
    return rows;
  }

  function tableHtml() {
    var rows = filteredSorted();
    if (!rows.length) {
      return '<div class="empty-state"><h3>沒有符合的選手</h3>' +
        "<p>請調整路線或賽區篩選。</p></div>";
    }
    var pinned = rows.filter(function (r) { return state.favs[r.puuid]; });
    var rest = rows.filter(function (r) { return !state.favs[r.puuid]; });
    var body = pinned.map(function (r) { return rowHtml(r, true); }).join("") +
      rest.map(function (r) { return rowHtml(r, false); }).join("");
    return '<div class="dpm-table">' +
      '<div class="dpm-head">' + headerHtml() + "</div>" +
      '<div class="dpm-body">' + body + "</div></div>";
  }

  function emptyHtml() {
    return '<div class="empty-state"><h3>尚無積分資料</h3>' +
      "<p>點擊右上角「⟳ SCRAPE」從 dpm.lol 爬取各伺服器職業選手積分；" +
      "首次回填約需 1–2 小時（每 1.5 秒一請求）。</p></div>";
  }

  /* ----------------------------------------------------- 每日戰況視圖 */
  var WEEK_ZH = ["日", "一", "二", "三", "四", "五", "六"];

  function dayWindow() {
    // 依目前日期分頁回 [起, 訖) 毫秒與標籤（昨天有專屬日界）
    var b = state.daily.bounds;
    if (state.days === "y")
      return { start: b.y_start, end: b.y_end, label: b.y_label, isDay: true };
    if (state.days === "d3")
      return { start: b.d3_start, end: b.now, label: b.d3_label, isDay: false };
    return { start: b.d7_start, end: b.now, label: b.d7_label, isDay: false };
  }

  function kstWeekday(ms) {
    // 邊界本身是 KST 00:00，加回 9 小時再取 UTC 星期即為 KST 星期
    return WEEK_ZH[new Date(ms + 9 * 3600 * 1000).getUTCDay()];
  }

  function inWindow(ts, win) {
    return ts >= win.start && ts < win.end;
  }

  function dailyChampsHtml(games, win) {
    var icons = games.filter(function (g) { return inWindow(g[0], win); })
      .map(function (g) {
        var champ = g[1], winFlag = g[2];
        return '<img class="dt-champ ' + (winFlag ? "is-win" : "is-loss") +
          '" src="/champ-img/' + encodeURIComponent(champ) + '.png" alt="' +
          esc(champ) + '" loading="lazy" onerror="this.style.display=\'none\'">';
      }).join("");
    return icons ? '<div class="dt-champs">' + icons + "</div>" : "";
  }

  function dailyPlayerHtml(pl, win) {
    var games = pl.games.filter(function (g) { return inWindow(g[0], win); });
    var w = games.filter(function (g) { return g[2]; }).length;
    var l = games.length - w;
    var rank = pl.tier_zh
      ? '<span class="dt-rank" style="color:' + esc(pl.tier_color) + '">' +
        esc(pl.tier_zh) + " " + esc(pl.lp == null ? "" : pl.lp) + "</span>"
      : '<span class="dt-rank dt-rank-none">—</span>';
    var wl = games.length
      ? '<span class="dt-wl"><b class="is-win">' + w + "</b>-" +
        '<b class="is-loss">' + l + "</b></span>"
      : "";
    var name = '<a class="dt-name" data-dpm-nav href="/ladder/p/' +
      encodeURIComponent(pl.name) + '">' + esc(pl.name) + "</a>";
    var head = '<div class="dt-player-head">' +
      '<img class="dt-pos" src="/pos-img/' + pl.pos + '.svg" alt="' +
      esc(pl.pos) + '">' + name + rank + wl + "</div>";
    return '<div class="dt-player" data-pos="' + esc(pl.pos) + '">' +
      head + dailyChampsHtml(pl.games, win) + "</div>";
  }

  function teamCardHtml(team, win) {
    // 卡頭勝敗為全隊（不受路線篩選影響）；卡身只列符合路線篩選的選手
    var tw = 0, tl = 0, tg = 0;
    team.players.forEach(function (pl) {
      pl.games.forEach(function (g) {
        if (inWindow(g[0], win)) {
          tg++;
          if (g[2]) tw++; else tl++;
        }
      });
    });
    var shown = team.players.filter(function (pl) {
      return state.filter.pos === "all" || pl.pos === state.filter.pos;
    });
    if (!shown.length) return "";
    var acd = team.academy ? '<span class="dt-acd" title="二隊／青訓">二</span>' : "";
    var head = '<div class="dt-card-head">' +
      '<span class="dt-code">' + esc(team.team) + "</span>" +
      '<span class="dt-region">' + esc(team.region) + acd + "</span>" +
      '<span class="dt-team-wl">' + tw + "-" + tl + " " + tg + "場</span>" +
      "</div>";
    var body = shown.map(function (pl) {
      return dailyPlayerHtml(pl, win);
    }).join("");
    return '<div class="dt-card" data-region="' + esc(team.region) + '">' +
      head + '<div class="dt-card-body">' + body + "</div></div>";
  }

  function dailyHtml() {
    var b = state.daily.bounds;
    if (!b) {
      return '<div class="empty-state"><h3>尚無每日戰況資料</h3>' +
        "<p>點擊右上角「⟳ SCRAPE」從 dpm.lol 爬取對戰紀錄。</p></div>";
    }
    var win = dayWindow();
    var teams = state.daily.teams.filter(function (t) {
      return state.filter.region === "all" ||
        t.region === state.filter.region;
    });
    var active = 0;
    var cards = teams.map(function (t) {
      var any = t.players.some(function (pl) {
        return pl.games.some(function (g) { return inWindow(g[0], win); });
      });
      if (any) active++;
      return teamCardHtml(t, win);
    }).join("");
    var period = win.isDay
      ? win.label + "（星期" + kstWeekday(win.start) + "）"
      : win.label;
    var info = document.getElementById("dpm-daily-info");
    if (info) {
      info.innerHTML = "顯示 " + period + "（韓服日界）・" + teams.length +
        " 隊（" + active + " 隊有打積分）・<b class='is-win'>勝 藍</b>・" +
        "<b class='is-loss'>敗 紅</b>・點選手名看完整逐場";
    }
    if (!cards) {
      return '<div class="empty-state"><h3>沒有符合的隊伍</h3>' +
        "<p>請調整賽區或路線篩選。</p></div>";
    }
    return '<div class="dpm-daily-grid">' + cards + "</div>";
  }

  /* --------------------------------------------------------- 分頁分派 */
  function draw() {
    var isDaily = state.tab === "daily";
    var subL = document.getElementById("dpm-sub-ladder");
    var subD = document.getElementById("dpm-sub-daily");
    var info = document.getElementById("dpm-daily-info");
    if (subL) subL.style.display = isDaily ? "none" : "";
    if (subD) subD.style.display = isDaily ? "" : "none";
    if (info) info.style.display = isDaily ? "" : "none";
    document.querySelectorAll(".dpm-tab[data-tab]").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-tab") === state.tab);
    });
    var host = document.getElementById("dpm-ladder-body");
    if (!host) return;
    if (isDaily) host.innerHTML = dailyHtml();
    else host.innerHTML = state.rows.length ? tableHtml() : emptyHtml();
  }

  function render(data) {
    var host = document.getElementById("dpm-ladder-body");
    if (!host) return "";
    if (data && Object.prototype.hasOwnProperty.call(data, "rows"))
      state.rows = data.rows || [];
    if (data && Object.prototype.hasOwnProperty.call(data, "teams"))
      state.daily = { bounds: data.bounds, teams: data.teams || [] };
    loadFavs();
    draw();
    return String(Date.now());
  }

  /* --------------------------------------------------------- 爬取狀態 */
  function fmtDoneTime(epochSec) {
    // epoch 秒 → MM-DD HH:MM；無效值回空字串
    var t = Number(epochSec);
    if (!isFinite(t) || t <= 0) return "";
    var d = new Date(t * 1000);
    var p2 = function (x) { return String(x).padStart(2, "0"); };
    return p2(d.getMonth() + 1) + "-" + p2(d.getDate()) + " " +
      p2(d.getHours()) + ":" + p2(d.getMinutes());
  }

  function pickErrors(raw) {
    // 只取錯誤數（後端存 Python dict 字串），其餘計數不展示
    var m = String(raw || "").match(/'errors'\s*:\s*(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }

  function renderStatus(s) {
    if (!s) return "";
    if (s.thread_alive) {
      var parts = (s.stage_progress || "").split("|");
      var zh = STAGE_ZH[parts[0]] || parts[0] || "";
      var prog = parts.length === 3
        ? "（" + parts[1] + "/" + parts[2] + "）"
        : "";
      return "爬取中：" + zh + prog + "…";
    }
    if (s.stage === "running")
      return "上次排程中斷，可再按 ⟳ SCRAPE";
    if (s.stage === "done") {
      var when = fmtDoneTime(s.last_success_at);
      var text = "上次完成" + (when ? "（" + when + "）" : "");
      // 僅在有錯誤時額外標示，零錯誤不顯示計數
      var errs = pickErrors(s.last_run_summary);
      if (errs) text += "：有 " + errs + " 個錯誤，詳見 scrape.log";
      return text;
    }
    if (s.stage === "failed")
      return "爬取失敗：" + (s.last_error || "未知錯誤");
    return "";
  }

  /* --------------------------------------------------------- 事件委派 */
  function installEvents() {
    if (window.__dpmLadderEvents) return;
    window.__dpmLadderEvents = true;

    document.addEventListener("click", function (e) {
      var tabEl = e.target.closest && e.target.closest(".dpm-tab[data-tab]");
      if (tabEl && !tabEl.classList.contains("is-disabled")) {
        state.tab = tabEl.getAttribute("data-tab");
        draw();
        return;
      }
      var dayEl = e.target.closest && e.target.closest(".dpm-day-pill[data-days]");
      if (dayEl) {
        var dayRow = dayEl.parentElement;
        dayRow.querySelectorAll(".dpm-day-pill").forEach(function (p) {
          p.classList.remove("active");
        });
        dayEl.classList.add("active");
        state.days = dayEl.getAttribute("data-days");
        draw();
        return;
      }
      var pill = e.target.closest &&
        e.target.closest(".dpm-pill[data-pos],.dpm-pill[data-region]");
      if (pill && !pill.classList.contains("is-disabled")) {
        var group = pill.parentElement;
        group.querySelectorAll(".dpm-pill").forEach(function (p) {
          p.classList.remove("active");
        });
        pill.classList.add("active");
        if (pill.hasAttribute("data-pos")) {
          state.filter.pos = pill.getAttribute("data-pos");
        } else {
          state.filter.region = pill.getAttribute("data-region");
        }
        render();
        return;
      }
      var sortEl = e.target.closest && e.target.closest(".dpm-th.sortable");
      if (sortEl) {
        var key = sortEl.getAttribute("data-sort");
        if (state.sort.key === key) {
          state.sort.dir = state.sort.dir === "desc" ? "asc" : "desc";
        } else {
          state.sort.key = key;
          state.sort.dir = "desc";
        }
        render();
        return;
      }
      var favEl = e.target.closest && e.target.closest(".dpm-fav");
      if (favEl) {
        var p = favEl.getAttribute("data-puuid");
        if (state.favs[p]) delete state.favs[p];
        else state.favs[p] = true;
        saveFavs();
        render();
      }
    });
  }

  /* 站內動態路由：以 pushState＋popstate 驅動 dcc.Location，避免整頁重載 */
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-dpm-nav]");
    if (!a || e.defaultPrevented) return;
    e.preventDefault();
    var href = a.getAttribute("href");
    if (href === window.location.pathname) return;
    window.history.pushState({}, "", href);
    window.dispatchEvent(new PopStateEvent("popstate"));
  });

  installEvents();
  window.dpmLadder = { render: render, renderStatus: renderStatus };
})();
