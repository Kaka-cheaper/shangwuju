/* =====================================================================
   晌午局 · 项目导览  —  交互引擎 (data-driven)
   用法：模块页只需定义 TOUR_DATA 后调用 TourEngine.mount(TOUR_DATA)。
   引擎负责：左轨步骤 · 中台 gap→reveal→quiz · 右图节点逐步点亮 ·
            进度持久化(localStorage) · 键盘导航 · 收尾解锁 · 模块间页脚。
   设计原则（来自 scan-tour 认知约束）：先抛问题→可选预测→揭晓+点亮节点。
   ===================================================================== */
(function (global) {
  "use strict";

  /* ---------- 小工具 ---------- */
  const $ = (sel, el = document) => el.querySelector(sel);
  const ce = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ---------- 轻量语义着色 ---------- */
  const KW = {
    py: ["def","class","return","if","elif","else","for","while","try","except","finally","with","as","import","from","async","await","yield","lambda","raise","pass","in","not","and","or","is","None","True","False","self","cls","global","nonlocal","assert","del","match","case"],
    js: ["function","return","if","else","for","while","try","catch","finally","const","let","var","class","extends","new","async","await","yield","import","export","from","default","typeof","instanceof","this","null","undefined","true","false","interface","type","enum","public","private","readonly","of","in","void","switch","case","break","continue"]
  };
  function highlight(code, lang) {
    lang = (lang === "py" || lang === "python") ? "py" : "js";
    const reComment = lang === "py" ? "#[^\\n]*" : "\\/\\/[^\\n]*";
    const kw = KW[lang].join("|");
    const re = new RegExp(
      `(${reComment})|("(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|\`(?:\\\\.|[^\`\\\\])*\`)|(@[A-Za-z_]\\w*)|\\b(\\d[\\w.]*)\\b|\\b(${kw})\\b|([A-Za-z_]\\w*)(?=\\s*\\()`,
      "g"
    );
    let out = "", last = 0, m;
    while ((m = re.exec(code))) {
      out += esc(code.slice(last, m.index));
      if (m[1]) out += `<span class="t-com">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="t-str">${esc(m[2])}</span>`;
      else if (m[3]) out += `<span class="t-fn">${esc(m[3])}</span>`;
      else if (m[4]) out += `<span class="t-num">${esc(m[4])}</span>`;
      else if (m[5]) out += `<span class="t-kw">${esc(m[5])}</span>`;
      else if (m[6]) out += `<span class="t-fn">${esc(m[6])}</span>`;
      last = re.lastIndex;
    }
    out += esc(code.slice(last));
    return out;
  }
  function renderCode(snippet, lang, startLine) {
    const lines = String(snippet).replace(/\n$/, "").split("\n");
    let n = startLine || 1, rows = "";
    for (const line of lines) {
      rows += `<div class="ln-row"><span class="gutter">${n++}</span><span class="src">${highlight(line, lang) || "&nbsp;"}</span></div>`;
    }
    return `<pre class="code">${rows}</pre>`;
  }

  /* ---------- 引擎 ---------- */
  const TourEngine = {
    data: null, i: 0, revealed: new Set(), answered: {}, nodeEls: {}, edgeEls: [],

    mount(data) {
      this.data = data;
      document.title = `${data.meta.title} · 晌午局导览`;
      this._buildChrome();
      this._buildRail();
      this._buildMap();
      this._restore();
      this._go(this.i, true);
      this._bindKeys();
      window.addEventListener("resize", () => this._layoutEdges());
    },

    /* —— 顶栏 + 三栏骨架 —— */
    _buildChrome() {
      const d = this.data, m = d.meta;
      const top = ce("div", "tour-top");
      top.innerHTML =
        `<a class="brand" href="index.html"><span class="dot"></span>晌午局 · 导览</a>` +
        `<span class="crumb">tour://<b>${esc(m.id)}</b></span>` +
        `<span class="spacer"></span>` +
        `<a class="top-link" href="index.html">⌂ 总览</a>` +
        (m.arch ? `<a class="top-link" href="architecture.html">为什么这样设计</a>` : "");
      const shell = ce("div", "tour-shell");
      shell.innerHTML =
        `<aside class="tour-rail"><div class="rail-progress"><div class="bar"><span></span></div><div class="pct"></div></div>` +
        `<div class="rail-head">${esc(m.module_label || "导览步骤")}</div><div class="rail-list"></div></aside>` +
        `<main class="tour-stage"></main>` +
        `<aside class="tour-map-wrap"><div class="map-head"><span class="blip"></span>代码地图 · 逐步点亮</div>` +
        `<div class="map-canvas"><svg class="edges"><defs><linearGradient id="flowgrad" x1="0" y1="0" x2="1" y2="1">` +
        `<stop offset="0" stop-color="#38e0d6"/><stop offset="1" stop-color="#ff8a3d"/></linearGradient></defs></svg></div>` +
        `<div class="map-legend"></div></aside>`;
      const foot = ce("div", "tour-foot");
      foot.innerHTML =
        (m.prev ? `<a class="foot-link prev" href="${esc(m.prev.href)}"><div class="dir">◂ 上一模块</div><div class="ttl">${esc(m.prev.title)}</div></a>` : `<span style="flex:1"></span>`) +
        (m.next ? `<a class="foot-link next" href="${esc(m.next.href)}"><div class="dir">下一模块 ▸</div><div class="ttl">${esc(m.next.title)}</div></a>` : `<span style="flex:1"></span>`);
      document.body.append(top, shell, foot);
      this.el = { stage: $(".tour-stage"), railList: $(".rail-list"), bar: $(".rail-progress .bar > span"), pct: $(".rail-progress .pct") };
    },

    /* —— 左轨步骤 —— */
    _buildRail() {
      const list = this.el.railList;
      this.data.steps.forEach((s, idx) => {
        const r = ce("div", "rail-step" + (s.kind === "skeleton" ? " skeleton" : ""));
        r.innerHTML = `<span class="idx">${s.kind === "skeleton" ? "◆" : idx}</span><span class="rs-label">${esc(s.title)}</span>`;
        r.addEventListener("click", () => { if (this.revealed.has(idx) || idx <= this._maxReached()) this._go(idx); });
        list.appendChild(r);
      });
    },

    /* —— 右图：节点 + 连线 —— */
    _buildMap() {
      const g = this.data.graph;
      if (!g || !g.nodes || !g.nodes.length) { $(".tour-map-wrap").classList.add("hide"); return; }
      const canvas = $(".map-canvas");
      // 自动布局兜底：节点未给 col/row 时按声明顺序竖排
      g.nodes.forEach((n, idx) => { if (n.col == null && n.row == null) { n.col = 0; n.row = idx; } });
      const cols = Math.max(...g.nodes.map(n => (n.col || 0))) + 1;
      const rows = Math.max(...g.nodes.map(n => (n.row || 0))) + 1;
      g.nodes.forEach(n => {
        const x = ((n.col || 0) + 0.5) / cols * 100;
        const y = ((n.row || 0) + 0.5) / rows * 100;
        const el = ce("div", "map-node k-" + (n.kind || "compute"));
        el.style.left = x + "%"; el.style.top = y + "%";
        el.dataset.x = x; el.dataset.y = y;
        el.innerHTML = `<span class="nd-kind">${esc(n.kind || "")}</span>${esc(n.label)}`;
        el.title = n.label;
        canvas.appendChild(el);
        this.nodeEls[n.id] = el;
      });
      const svg = $(".map-canvas svg.edges");
      (g.edges || []).forEach(e => {
        const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("class", "map-edge");
        svg.appendChild(p);
        this.edgeEls.push({ el: p, from: e.from, to: e.to });
      });
      // 图例
      const legend = $(".map-legend");
      const kinds = [["k-input","输入","#38e0d6"],["k-compute","计算","#38e0d6"],["k-decision","决策/分支","#9d8cff"],["k-output","产出","#ff8a3d"],["k-fallback","兜底","#4ade9b"]];
      legend.innerHTML = kinds.filter(k => g.nodes.some(n => "k-" + (n.kind||"compute") === k[0]))
        .map(k => `<span><i style="background:${k[2]}"></i>${k[1]}</span>`).join("");
      requestAnimationFrame(() => this._layoutEdges());
    },

    _layoutEdges() {
      const canvas = $(".map-canvas"); if (!canvas) return;
      const W = canvas.clientWidth, H = canvas.clientHeight;
      const svg = $(".map-canvas svg.edges");
      if (svg) { svg.setAttribute("width", W); svg.setAttribute("height", H); }
      const center = id => { const n = this.nodeEls[id]; return n ? { x: parseFloat(n.dataset.x) / 100 * W, y: parseFloat(n.dataset.y) / 100 * H } : null; };
      this.edgeEls.forEach(e => {
        const a = center(e.from), b = center(e.to); if (!a || !b) return;
        const dx = (b.x - a.x) * 0.5;
        e.el.setAttribute("d", `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`);
      });
    },

    /* —— 渲染某一步 —— */
    _go(i, initial) {
      if (i < 0 || i >= this.data.steps.length) { if (i >= this.data.steps.length) return this._finish(); return; }
      this.i = i;
      const s = this.data.steps[i];
      const skeleton = s.kind === "skeleton";
      const already = this.revealed.has(i) || skeleton;
      if (skeleton) this.revealed.add(i);

      // 中台
      const stage = this.el.stage;
      stage.innerHTML = "";
      const card = ce("div", "step-card");
      card.innerHTML =
        `<div class="stage-eyebrow">${skeleton ? "主线骨架 · MAIN LINE" : "第 " + i + " 站 / 共 " + (this.data.steps.length - 1)}</div>` +
        `<h2>${esc(s.title)}</h2>` +
        (s.kicker ? `<div class="step-kicker">${esc(s.kicker)}</div>` : "");

      if (s.gap && !skeleton) {
        card.appendChild(ce("div", "gap-block", `<div class="lbl">缺口 · the gap</div><div class="q">${esc(s.gap)}</div>`));
      }

      // quiz（在揭晓前预测）
      let quizDone = !s.quiz || this.answered[i] != null;
      if (s.quiz && !skeleton) card.appendChild(this._buildQuiz(s.quiz, i, () => { quizDone = true; this._syncReveal(card, true); }));

      // reveal
      const rev = ce("div", "reveal-block" + (already ? " show" : ""));
      rev.innerHTML =
        `<div class="lbl">揭晓 · reveal</div><div class="r">${s.reveal || ""}</div>` +
        (s.teach ? `<div class="teach-block"><div class="lbl">掰开揉碎 · 老师讲</div>` +
          (Array.isArray(s.teach) ? s.teach : [s.teach]).map(p => `<p>${p}</p>`).join("") + `</div>` : "") +
        (s.refs && s.refs.length ? `<div class="refs">${s.refs.map(r => this._ref(r)).join("")}</div>` : "") +
        (s.code ? this._dig(s.code) : "");
      card.appendChild(rev);

      // 导航
      const nav = ce("div", "stage-nav");
      const revealBtn = ce("button", "btn primary", already ? (i >= this.data.steps.length - 1 ? "走完 · 解锁全图 ✦" : "下一站 ▸") : "揭晓答案 ✦");
      const prevBtn = ce("button", "btn ghost", "◂ 上一步");
      prevBtn.disabled = i === 0;
      prevBtn.addEventListener("click", () => this._go(i - 1));
      revealBtn.addEventListener("click", () => {
        if (!this.revealed.has(i) && !skeleton) {
          this.revealed.add(i); rev.classList.add("show");
          revealBtn.textContent = i >= this.data.steps.length - 1 ? "走完 · 解锁全图 ✦" : "下一站 ▸";
          this._updateMap(); this._updateRail(); this._save();
        } else {
          this._go(i + 1);
        }
      });
      nav.append(prevBtn, revealBtn);
      nav.appendChild(ce("span", "nav-hint", "← → 切换 · Enter 揭晓/前进"));
      card.appendChild(nav);
      stage.appendChild(card);

      this._updateMap();
      this._updateRail();
      if (!initial) stage.scrollIntoView({ behavior: "smooth", block: "start" });
      requestAnimationFrame(() => this._layoutEdges());
    },

    _syncReveal() {/* placeholder for quiz→reveal hinting; reveal stays gated by button */},

    _buildQuiz(quiz, idx, onAnswer) {
      const box = ce("div", "quiz");
      box.innerHTML = `<div class="qz-lbl">预测一下 · quiz</div><div class="qz-q">${esc(quiz.question)}</div>`;
      const note = ce("div", "qz-note");
      const opts = [];
      quiz.options.forEach((opt, oi) => {
        const b = ce("button", "qz-opt", `<span class="mk">${String.fromCharCode(65 + oi)}</span><span>${esc(opt)}</span>`);
        b.addEventListener("click", () => {
          if (this.answered[idx] != null) return;
          this.answered[idx] = oi;
          opts.forEach(x => x.disabled = true);
          const correct = oi === quiz.answer;
          b.classList.add(correct ? "correct" : "wrong");
          if (!correct) opts[quiz.answer].classList.add("correct");
          note.className = "qz-note show" + (correct ? " ok" : "");
          note.innerHTML = correct
            ? `<b>答对了。</b> ${esc(quiz.right_note || quiz.wrong_note || "")}`
            : `${esc(quiz.wrong_note || "")}`;
          this._save(); onAnswer && onAnswer();
        });
        opts.push(b); box.appendChild(b);
      });
      box.appendChild(note);
      // 复原已答状态
      if (this.answered[idx] != null) {
        const oi = this.answered[idx], correct = oi === quiz.answer;
        opts.forEach(x => x.disabled = true);
        opts[oi].classList.add(correct ? "correct" : "wrong");
        if (!correct) opts[quiz.answer].classList.add("correct");
        note.className = "qz-note show" + (correct ? " ok" : "");
        note.innerHTML = correct ? `<b>答对了。</b> ${esc(quiz.right_note || quiz.wrong_note || "")}` : `${esc(quiz.wrong_note || "")}`;
      }
      return box;
    },

    _ref(r) {
      const lines = r.lines ? (r.lines[0] === r.lines[1] ? r.lines[0] : r.lines[0] + "–" + r.lines[1]) : "";
      return `<span class="ref-chip" title="${esc(r.note || r.file)}">${esc(r.file)}${lines ? `<span class="ln">:${lines}</span>` : ""}</span>`;
    },

    _dig(code) {
      const start = code.lines ? code.lines[0] : 1;
      const fileLabel = code.file ? `<span class="dig-file"><b>${esc(code.file)}</b>${code.lines ? ":" + code.lines[0] + "–" + code.lines[1] : ""}</span>` : "";
      return `<details class="dig"><summary><span class="chev">▸</span>${esc(code.label || "展开源码 · 按需深挖")}${fileLabel}</summary>` +
        `<div class="code-wrap">${renderCode(code.snippet, code.lang, start)}</div></details>`;
    },

    /* —— 地图点亮（累积） —— */
    _litSet() {
      const lit = new Set();
      this.data.steps.forEach((s, idx) => { if (this.revealed.has(idx)) (s.focus || []).forEach(f => lit.add(f)); });
      return lit;
    },
    _updateMap() {
      if (!Object.keys(this.nodeEls).length) return;
      const lit = this._litSet();
      const cur = new Set(this.data.steps[this.i].focus || []);
      Object.entries(this.nodeEls).forEach(([id, el]) => {
        el.classList.toggle("lit", lit.has(id));
        el.classList.toggle("dim", !lit.has(id) && !cur.has(id));
        el.style.zIndex = cur.has(id) ? 5 : 1;
      });
      this.edgeEls.forEach(e => e.el.classList.toggle("lit", lit.has(e.from) && lit.has(e.to)));
    },
    _updateRail() {
      const items = this.el.railList.children;
      for (let k = 0; k < items.length; k++) {
        items[k].classList.toggle("active", k === this.i);
        items[k].classList.toggle("done", this.revealed.has(k) && k !== this.i);
      }
      const total = this.data.steps.length;
      const pct = Math.round(this.revealed.size / total * 100);
      this.el.bar.style.width = pct + "%";
      this.el.pct.textContent = `${this.revealed.size} / ${total} 站 · ${pct}%`;
    },
    _maxReached() { return this.revealed.size ? Math.max(...this.revealed) + 1 : 0; },

    /* —— 收尾 · 解锁自由探索 —— */
    _finish() {
      this.data.steps.forEach((_, idx) => this.revealed.add(idx));
      this._updateMap(); this._updateRail(); this._save();
      Object.values(this.nodeEls).forEach(el => { el.classList.add("lit"); el.classList.remove("dim"); });
      this.edgeEls.forEach(e => e.el.classList.add("lit"));
      const m = this.data.meta;
      this.el.stage.innerHTML =
        `<div class="tour-end"><div class="seal">✦ 本模块导览完成 · 全图已点亮</div>` +
        `<h2>${esc(m.outro_title || "你已掌握这一段主线")}</h2>` +
        `<p>${esc(m.outro || "代码地图已全部点亮，可点左侧任意步骤回看；或顺着主线进入下一模块。")}</p>` +
        `<div class="end-actions">` +
        `<button class="btn" onclick="TourEngine._go(0)">↺ 重走本模块</button>` +
        (m.next ? `<a class="btn primary" href="${esc(m.next.href)}">继续 · ${esc(m.next.title)} ▸</a>` : `<a class="btn primary" href="index.html">⌂ 回到总览</a>`) +
        `</div></div>`;
      this.el.stage.scrollIntoView({ behavior: "smooth", block: "start" });
    },

    /* —— 持久化 —— */
    _key() { return "shangwuju_tour_" + this.data.meta.id; },
    _save() { try { localStorage.setItem(this._key(), JSON.stringify({ i: this.i, revealed: [...this.revealed], answered: this.answered })); } catch (e) {} },
    _restore() {
      try {
        const raw = localStorage.getItem(this._key()); if (!raw) return;
        const o = JSON.parse(raw);
        this.revealed = new Set(o.revealed || []); this.answered = o.answered || {};
        this.i = Math.min(o.i || 0, this.data.steps.length - 1);
      } catch (e) {}
    },

    _bindKeys() {
      document.addEventListener("keydown", e => {
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
        if (e.key === "ArrowRight") { e.preventDefault(); const b = $(".stage-nav .btn.primary"); b && b.click(); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); this._go(this.i - 1); }
        else if (e.key === "Enter") { const b = $(".stage-nav .btn.primary"); b && b.click(); }
      });
    }
  };

  global.TourEngine = TourEngine;
  global.renderTourCode = renderCode; // 供 index/architecture 复用
})(window);
