/* 選手積分詳情頁（圖3／圖4／圖5）客戶端渲染。
   隊友列、合併帳號、篩選 chips 與逐場明細皆以 innerHTML 產出。 */
(function () {
  "use strict";

  var state = {
    name: null,
    detail: null,
    filters: { champ: null, opp: null, vs: null },
  };

  var CHIP_META = [
    { key: "champ", label: "英雄" },
    { key: "opp", label: "對位英雄" },
    { key: "vs", label: "對位人員" },
  ];

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function champSrc(name) {
    return "/champ-img/" + encodeURIComponent(name) + ".png";
  }

  /* ----------------------------------------------------------- 圖4 隊友 */
  function matesHtml(d) {
    var logo = d.team_logo
      ? '<img class="dpm-mate-logo" src="' + esc(d.team_logo) +
        '" alt="" onerror="this.style.display=\'none\'">'
      : "";
    var mates = [{ display_name: d.display_name, pos: d.pos }]
      .concat(d.teammates || [])
      .sort(function (a, b) {
        return a.display_name.localeCompare(b.display_name);
      });
    var chips = mates.map(function (m) {
      var active = m.display_name === d.display_name ? " active" : "";
      var icon = m.pos
        ? '<img src="/pos-img/' + m.pos + '.svg" alt="">'
        : "";
      return '<a class="dpm-mate' + active + '" data-dpm-nav ' +
        'href="/ladder/p/' + encodeURIComponent(m.display_name) + '">' +
        icon + esc(m.display_name) + "</a>";
    }).join("");
    return logo + chips;
  }

  /* ----------------------------------------------------- 圖5 帳號＋篩選 */
  function accountPill(a) {
    var inner =
      '<span class="srv">' + esc(a.server) + "</span>" +
      esc(a.game_name) + "#" + esc(a.tag_line) + " " +
      '<span style="color:' + esc(a.tier_color) + '">' +
      esc(a.tier_zh) + "</span> " +
      '<span class="lp">' + (a.lp == null ? "" : a.lp) + "</span>";
    if (a.profile_url) {
      return '<a class="dpm-acct-pill" href="' + esc(a.profile_url) +
        '" target="_blank" rel="noopener noreferrer">' + inner + "</a>";
    }
    return '<span class="dpm-acct-pill">' + inner + "</span>";
  }

  function chipHtml(meta) {
    var value = state.filters[meta.key];
    var label = value || meta.label;
    return '<span class="dpm-chip" data-chip="' + meta.key + '">' +
      '<span class="dpm-chip-label">' + esc(label) + "</span>" +
      '<span class="x" data-chip-clear="' + meta.key + '">✕</span></span>';
  }

  function infoHtml(d) {
    var accounts = (d.accounts || []).map(accountPill).join("");
    var posIcon = d.pos
      ? '<img class="dpm-info-pos" src="/pos-img/' + d.pos + '.svg" alt="">'
      : "";
    var chips = CHIP_META.map(chipHtml).join("");
    return '<div class="dpm-info-row">' + posIcon +
      '<span class="dpm-info-name">' + esc(d.display_name) + "</span>" +
      '<span class="dpm-info-merge">合併 ' +
      (d.accounts || []).length + " 帳號：</span>" + accounts + "</div>" +
      '<div class="dpm-info-row" style="padding-top:4px">' + chips + "</div>";
  }

  /* ----------------------------------------------------------- 圖3 明細 */
  var HEADERS = ["日期", "時長", "英雄", "對位英雄", "對位人員", "K/D/A",
    "參與率", "搶二", "金差@15", "經差@15", "召喚師", "符文", "技能",
    "出裝", "評分"];

  function signed(v) {
    if (v == null) return '<span class="dpm-muted">—</span>';
    var cls = v >= 0 ? "up" : "down";
    var sign = v > 0 ? "+" : "";
    return '<span class="dpm-pos-diff ' + cls + '">' + sign + v + "</span>";
  }

  function skillHtml(seq) {
    return (seq || "").split("").map(function (ch, i) {
      var color = ch === "R" ? "#ff6b57" : "#6aa5ff";
      var sep = i ? '<span class="dpm-muted">·</span>' : "";
      return sep + '<span style="color:' + color + '">' + ch + "</span>";
    }).join("");
  }

  function itemsHtml(m) {
    var cells = (m.items || []).slice(0, 7).map(function (u) {
      return u ? '<img src="' + esc(u) + '" alt="" loading="lazy">'
        : '<span class="slot"></span>';
    });
    while (cells.length < 7) cells.push('<span class="slot"></span>');
    return '<div class="dpm-items">' + cells.join("") + "</div>";
  }

  function matchRowHtml(m) {
    var champ = '<span class="dpm-champ-cell"><img src="' +
      esc(m.champ_icon) + '" alt="' + esc(m.champion) + '" loading="lazy">' +
      (m.duo_pos && m.duo_champ_icon
        ? '<img class="duo" src="' + esc(m.duo_champ_icon) + '" alt="">'
        : "") + "</span>";
    var oppChamp = m.opp_champ_icon
      ? '<img src="' + esc(m.opp_champ_icon) +
        '" alt="' + esc(m.opp_champion || "") + '" style="width:30px;' +
        'height:30px;border-radius:5px" loading="lazy">'
      : '<span class="dpm-muted">—</span>';
    var opponent = "—";
    if (m.opponent) {
      opponent = m.opponent.is_pro
        ? '<a class="dpm-op-link" data-dpm-nav href="/ladder/p/' +
          encodeURIComponent(m.opponent.name) + '">' +
          esc((m.opponent.team ? m.opponent.team + " " : "") +
            m.opponent.name) + "</a>"
        : '<span class="dpm-op-name">路人</span>';
    }
    var first2 = m.first2 == null
      ? '<span class="dpm-muted">—</span>'
      : m.first2
        ? '<span class="dpm-tick yes">✓</span>'
        : '<span class="dpm-tick no">✕</span>';
    var score = m.score == null
      ? '<span class="dpm-muted">—</span>'
      : '<span class="dpm-score" style="color:' + esc(m.score_color) + '">' +
        m.score + "</span>" +
        (m.score_rank ? ' <span class="rk">(#' + m.score_rank + ")</span>"
          : "");
    var resLabel = m.win
      ? '<span class="res-win">勝</span>'
      : '<span class="res-loss">敗</span>';
    var cells = [
      '<div class="dpm-mtd"><div class="dpm-mdate">' + resLabel +
        "<span>" + esc(m.date) + "</span>" +
        '<span class="t">' + esc(m.time) + "</span></div></div>",
      '<div class="dpm-mtd">' + esc(m.duration) + "</div>",
      '<div class="dpm-mtd">' + champ + "</div>",
      '<div class="dpm-mtd">' + oppChamp + "</div>",
      '<div class="dpm-mtd">' + opponent + "</div>",
      '<div class="dpm-mtd">' + m.k + " / " + m.d + " / " + m.a +
        ' <span class="dpm-muted">(' + m.kda_val + ")</span></div>",
      '<div class="dpm-mtd">' +
        (m.kp == null ? "—" : m.kp + "%") + "</div>",
      '<div class="dpm-mtd">' + first2 + "</div>",
      '<div class="dpm-mtd">' + signed(m.gd15) + "</div>",
      '<div class="dpm-mtd">' + signed(m.xd15) + "</div>",
      '<div class="dpm-mtd"><div class="dpm-icon-stack">' +
        '<img src="' + esc(m.spell1) + '" title="D" alt="" loading="lazy">' +
        '<img src="' + esc(m.spell2) + '" title="F" alt="" loading="lazy">' +
        "</div></div>",
      '<div class="dpm-mtd"><div class="dpm-icon-stack">' +
        '<img src="' + esc(m.rune) + '" alt="" loading="lazy"></div></div>',
      '<div class="dpm-mtd"><span class="dpm-skill">' +
        skillHtml(m.skill_order) + "</span></div>",
      '<div class="dpm-mtd">' + itemsHtml(m) + "</div>",
      '<div class="dpm-mtd">' + score + "</div>",
    ];
    return '<div class="dpm-mrow ' + (m.win ? "is-win" : "is-loss") + '">' +
      cells.join("") + "</div>";
  }

  function filtered(d) {
    return (d.matches || []).filter(function (m) {
      if (state.filters.champ && m.champion !== state.filters.champ)
        return false;
      if (state.filters.opp && m.opp_champion !== state.filters.opp)
        return false;
      if (state.filters.vs) {
        if (!m.opponent || m.opponent.name !== state.filters.vs)
          return false;
      }
      return true;
    });
  }

  function matchHtml(d) {
    var rows = filtered(d);
    var head = '<div class="dpm-mrow" style="min-height:34px">' +
      HEADERS.map(function (h) {
        return '<div class="dpm-mtd" style="color:#aebacb;font-size:12px;' +
          'border-bottom:1px solid var(--border)">' + h + "</div>";
      }).join("") + "</div>";
    var note = '<div style="padding:8px 14px;font-size:12px;color:var(--muted)">' +
      "🗂 單雙積分逐場　顯示 " + rows.length + " / " +
      (d.matches || []).length + " 場　最新在上・只收錄單雙排・" +
      '資料來源 <a href="https://dpm.lol" target="_blank" ' +
      'style="color:#6aa5ff">dpm.lol</a></div>';
    if (!rows.length) {
      return note + '<div class="empty-state"><h3>沒有符合的場次</h3>' +
        "<p>請清除英雄／對位英雄／對位人員篩選。</p></div>";
    }
    return '<div class="dpm-match-table">' + note + head +
      rows.map(matchRowHtml).join("") + "</div>";
  }

  function emptyHtml(name) {
    return '<div class="empty-state"><h3>查無此選手的積分資料</h3>' +
      "<p>" + esc(name || "") +
      " 尚未出現在已爬取的 dpm.lol 排行榜；待下次 SCRAPE 後重試。</p></div>";
  }

  function render(data) {
    var mates = document.getElementById("dpm-mates-bar");
    var info = document.getElementById("dpm-info-host");
    var host = document.getElementById("dpm-match-host");
    if (!mates || !info || !host) return "";
    closePopover();
    if (!data || !data.found) {
      state.detail = null;
      mates.innerHTML = "";
      info.innerHTML = "";
      host.innerHTML = emptyHtml(data && data.name);
      return String(Date.now());
    }
    if (state.name !== data.display_name) {
      state.name = data.display_name;
      state.filters = { champ: null, opp: null, vs: null };
    }
    state.detail = data;
    mates.innerHTML = matesHtml(data);
    info.innerHTML = infoHtml(data);
    host.innerHTML = matchHtml(data);
    return String(Date.now());
  }

  /* --------------------------------------------------------- 篩選彈窗 */
  var popEl = null;

  function closePopover() {
    if (popEl) { popEl.remove(); popEl = null; }
  }

  function optionValues(key) {
    var counts = {};
    (state.detail.matches || []).forEach(function (m) {
      var v = null;
      if (key === "champ") v = m.champion;
      else if (key === "opp") v = m.opp_champion;
      else if (key === "vs" && m.opponent) v = m.opponent.name;
      if (v) counts[v] = (counts[v] || 0) + 1;
    });
    return Object.keys(counts).sort(function (a, b) {
      return counts[b] - counts[a];
    }).slice(0, 40).map(function (v) {
      return { value: v, count: counts[v] };
    });
  }

  function openPopover(chipEl, key) {
    closePopover();
    var rect = chipEl.getBoundingClientRect();
    popEl = document.createElement("div");
    popEl.className = "dpm-pop";
    popEl.style.top = rect.bottom + 6 + "px";
    popEl.style.left = rect.left + "px";
    var iconKey = key === "vs" ? null : "champ";
    popEl.innerHTML = optionValues(key).map(function (o) {
      var icon = iconKey
        ? '<img src="' + champSrc(o.value) + '" alt="">'
        : "";
      return '<div class="dpm-opt" data-opt="' + key + '" data-val="' +
        esc(o.value) + '">' + icon + "<span>" + esc(o.value) + "</span>" +
        '<span class="n">' + o.count + "</span></div>";
    }).join("") || '<div class="dpm-pop-empty">無選項</div>';
    document.body.appendChild(popEl);
  }

  document.addEventListener("click", function (e) {
    var clear = e.target.closest &&
      e.target.closest("[data-chip-clear]");
    if (clear) {
      e.stopPropagation();
      var key = clear.getAttribute("data-chip-clear");
      state.filters[key] = null;
      closePopover();
      if (state.detail) render(state.detail);
      return;
    }
    var chip = e.target.closest && e.target.closest("[data-chip]");
    if (chip) {
      openPopover(chip, chip.getAttribute("data-chip"));
      return;
    }
    var opt = e.target.closest && e.target.closest("[data-opt]");
    if (opt) {
      state.filters[opt.getAttribute("data-opt")] =
        opt.getAttribute("data-val");
      closePopover();
      if (state.detail) render(state.detail);
      return;
    }
    if (popEl && !e.target.closest(".dpm-pop")) closePopover();
  });

  window.dpmDetail = { render: render };
})();
