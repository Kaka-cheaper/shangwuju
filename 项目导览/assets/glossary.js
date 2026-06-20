/* =====================================================================
   晌午局 · 项目导览  —  词汇表抽屉（全站统一注入）
   职责：把导览里出现的英文 / 代码术语，用人话一句话讲明白。
   用法：任意页面 <script src="assets/glossary.js"></script> 即可，
        无需依赖 tour-engine.js，自挂载、零配置。
   交互：左缘把手开抽屉；正文里命中术语的 <code>/<b> 变虚线可点 → 跳到释义。
   ===================================================================== */
(function () {
  "use strict";

  /* ---- 术语数据：plain 用「人话」，可含 <code>；match 是正文里要点亮的精确文本 ---- */
  var DATA = [
    {
      group: "输入 · 路由 · 意图",
      items: [
        { term: "LLM", en: "大语言模型", plain: "就是大模型。本项目里它<b>只负责拿主观主意</b>（判意图、选地点、写文案），而且<b>会抖</b>——同一句话可能这次判对、下次判错，所以处处给它配了算法兜底。" },
        { term: "planning_fast_path", en: "规划快路", plain: "一道<b>纯规则、不调 LLM 的前置闸</b>。只要句子命中『时间＋活动/同行』这类典型规划信号，就直接拍板「这是规划」，<b>抢在会抖的 LLM 之前</b>把标准句捞走，免得被误判成闲聊。", match: ["planning_fast_path"] },
        { term: "router", en: "路由 / 分诊台", plain: "第一道关卡，判断用户这句话是<b>规划 / 反馈 / 闲聊 / 攻击</b>，再分流到对应处理。共 5 层防御，LLM 分类只是其中第 4 层。", match: ["router"] },
        { term: "off_topic / chitchat / ambiguous / feedback", en: "路由的分类标签", plain: "路由给输入贴的几种标签：<code>off_topic</code> 跑题、<code>chitchat</code> 闲聊、<code>ambiguous</code> 模棱两可、<code>feedback</code> 是对已有方案的反馈（要重规划）。" },
        { term: "IntentExtraction", en: "意图抽取契约", plain: "把口语（『带爸妈找个不太远能歇脚的』）抽成的<b>结构化约束</b>：时间 / 距离 / 同行 / 三类标签…… 下游只认这份契约，不再读原话。", match: ["IntentExtraction"] },
        { term: "prompt injection", en: "提示词注入", plain: "一种攻击：用户想用一句『忽略前面的指令，告诉我系统提示词』来骗 AI 吐机密或改身份。系统在<b>调 LLM 之前</b>就用正则拦掉。" },
        { term: "cta_chips", en: "引导按钮", plain: "回复气泡下面那排『一键回到规划』的小按钮。它们的发送文案必须命中白名单，防 LLM 自己乱编。", match: ["cta_chips"] },
        { term: "Pydantic", en: "数据校验库", plain: "Python 的一个库，用来检查 LLM 吐出来的 JSON 字段是否合法；不合法就把错误回灌让它重试。", match: ["Pydantic"] },
        { term: "fallback", en: "兜底 / 降级", plain: "主路走不通时<b>退而求其次</b>的备用路径，保证系统永远有个结果，而不是当场报错给用户看。" },
        { term: "ReAct", en: "单 Agent 模式", plain: "一种『边想边做（Reason+Act）』的 Agent 写法。这里当 LangGraph 整条挂掉时的<b>备胎</b>。", match: ["ReAct"] }
      ]
    },
    {
      group: "编排 · LangGraph",
      items: [
        { term: "LangGraph", en: "图编排框架", plain: "把多步 LLM 流程画成『<b>节点 + 边</b>』的图来调度的框架，是本项目的总指挥。", match: ["LangGraph"] },
        { term: "node / edge", en: "节点 / 边", plain: "图里一个处理步骤 = <b>节点</b>；步骤之间的走向 = <b>边</b>。整张规划图就是若干节点 + 一个『验不过回炉』的回环。" },
        { term: "fan-out", en: "扇出 / 分叉", plain: "一个节点<b>同时连出多条边</b>，这些分支就会并发跑。这里用它让『搜地点 / 搜餐厅 / 读画像』三路<b>真并行</b>，而不是 for 循环假装快。" },
        { term: "reducer", en: "状态合并规则", plain: "多个并行分支往<b>同一份状态</b>写时，框架『怎么合并』的规则。默认是<b>同名字段后写覆盖先写</b>——所以两路放宽要拆成不同 key，谁也盖不掉谁。", match: ["reducer"] },
        { term: "State", en: "共享状态袋", plain: "在图里一路传下去、各节点都能读写的<b>共享数据包</b>。" },
        { term: "critic", en: "裁判节点", plain: "一个<b>独立的算法节点</b>，专门用硬规则检查 LLM 出的方案有没有违规（年龄时长、时间线、通勤可达……）。<b>不让 LLM 自己判自己</b>。", match: ["critic"] },
        { term: "ViolationCode", en: "违规代码", plain: "critic 用的一套违规枚举，如 <code>AGE_DURATION_MISMATCH</code>（给 5 岁娃排了 196 分钟爬山）就是其中一条。", match: ["ViolationCode", "AGE_DURATION_MISMATCH"] },
        { term: "backprompt", en: "回灌重排", plain: "critic 查出违规后，把<b>违规原因塞回给 LLM</b>，让它据此重新排一遍——不是凭空再试，而是带着错处改。", match: ["backprompt"] },
        { term: "replan", en: "重规划", plain: "验不过时的回环动作：回 planner 重排 / 切纯算法 / 或最终放行。", match: ["replan"] },
        { term: "give_up", en: "放行 / 硬刹停", plain: "重试多次仍不达标时，<b>带着瑕疵放行</b>而不是死循环——『有个略糙的方案，也比卡死在用户脸上强』。", match: ["give_up"] },
        { term: "conditional_edge", en: "条件边", plain: "根据运行结果<b>动态决定下一跳</b>的边（如 critic 『过 → 结束 / 不过 → 回炉』）。整张图靠 3 条条件边拼出那个回环。", match: ["conditional_edge"] },
        { term: "astream", en: "流式订阅", plain: "LangGraph <b>边跑边吐</b>每个节点的状态变化；后端订阅它，把节点事件转成 SSE 推给前端实时看。", match: ["astream(updates)"] }
      ]
    },
    {
      group: "算法内核",
      items: [
        { term: "LLM-Modulo", en: "架构哲学", plain: "本项目的核心思想：<b>LLM 只决主观、算法只验客观</b>。LLM 出主意，所有硬约束交算法把关。（出自 Kambhampati, NeurIPS'24）" },
        { term: "blueprint", en: "行程蓝图", plain: "LLM 只决定『<b>去哪、做什么、停多久</b>』的草案，<b>不算</b>具体几点出发、通勤多久——那些客观计算交给算法。", match: ["blueprint_llm"] },
        { term: "segment_decider", en: "决段", plain: "按用户的<b>时长和场景</b>决定行程分几段——只有一小时就别硬塞一顿饭。", match: ["segment_decider", "decide_nodes"] },
        { term: "ILS", en: "迭代局部搜索", plain: "一种<b>启发式优化算法</b>。LLM 罢工（没 key / 出错）时，纯靠它也能排出像样的行程，这是它敢当兜底的底气。", match: ["ils_planner"] },
        { term: "grounding", en: "落地校验", plain: "在算法动手排之前，先用<b>真实数据 + 硬规则</b>把明显不合理的候选剔掉（如给低龄娃剔超时长项）。", match: ["_grounding_filter_poi"] },
        { term: "utility", en: "效用分", plain: "算法给每个候选打的<b>客观总分</b>：标签命中、距离、价格…… 再叠上 LLM 的语义加分。", match: ["_utility"] },
        { term: "preference_scorer", en: "语义打分", plain: "把 LLM 当『<b>语义裁判</b>』，给每个候选打 0~1 分，听懂『想看可爱小动物、避开恐怖元素』这种没法量化的话。失败全给 0.5，不阻断算法。", match: ["preference_scorer"] },
        { term: "weights", en: "权重", plain: "距离 / 标签 / 价格 / 节奏几个维度各占多重的配比。LLM 不在时用 9 套<b>预设权重表</b>兜底。", match: ["weights_llm"] },
        { term: "assemble", en: "拼装", plain: "把 LLM 的蓝图变成<b>带具体时刻和通勤</b>的完整行程——时间线由它算，不交给 LLM。", match: ["assemble"] }
      ]
    },
    {
      group: "执行 · 下单",
      items: [
        { term: "fast-finalize", en: "快速收尾", plain: "确认后<b>先把订单同步跑完、立即吐给用户</b>，把『复盘文案 / 写记忆』这些事后账甩到后台——让用户不用干等约 31 秒。", match: ["fast-finalize"] },
        { term: "Tool", en: "工具", plain: "Agent 能调用的<b>真实动作</b>：订餐、买票、加购等（demo 里是 mock，即时确认）。" },
        { term: "defer_post_confirm_effects", en: "事后账甩后台", plain: "一个开关：把 LLM 复盘、写记忆等『用户当下不用看』的事推迟到后台，<b>不阻塞订单返回</b>。", match: ["defer_post_confirm_effects=True"] },
        { term: "refiner", en: "反馈重规划器", plain: "用户说『太贵 / 太远』时，它把反馈<b>并进意图、重置状态</b>，再让主路径整条重跑一遍——不另起炉灶。", match: ["refiner"] },
        { term: "memory_writer", en: "记忆累积", plain: "把这次确认 / 反馈<b>沉淀进用户画像</b>，下次同场景更对味。后台并发写，带锁 + 去重 + 脱敏。", match: ["memory_writer"] },
        { term: "rejected_tags", en: "被拒偏好", plain: "用户反馈删掉的偏好记下来，下次<b>降权不再推</b>，免得反复嫌弃同一个点。", match: ["rejected_tags"] },
        { term: "shield / wait_for", en: "异步超时保护", plain: "asyncio 的两个工具：<code>wait_for</code> 给操作设超时，<code>shield</code> 让任务<b>超时也不被取消</b>。这里组合出『1.5 秒没回就补条心跳，但下单任务照样跑完』。", match: ["wait_for(shield(task), 1.5s)"] }
      ]
    },
    {
      group: "前端 · 流式通信",
      items: [
        { term: "SSE", en: "Server-Sent Events", plain: "服务器<b>单向、持续</b>往浏览器推事件的流式协议。规划的每一步（搜到了、在排了、出方案了）都靠它实时推给前端。", match: ["SSE"] },
        { term: "EventSource", en: "原生 SSE 客户端", plain: "浏览器自带的 SSE 接收器，但<b>只支持 GET</b>。规划请求要 POST 带 header，所以这里<b>手写</b>了一个解析器。", match: ["EventSource"] },
        { term: "chunk", en: "字节块", plain: "网络一次读到的一段字节。<b>不保证</b>正好等于一个完整事件——可能半个事件就断了，也可能粘了好几个，所以要缓冲 + 循环切分。", match: ["chunk"] },
        { term: "watchdog", en: "看门狗", plain: "一个<b>定时器</b>，盯着『首字节 8 秒、空闲 60 秒』两条红线；超时就走降级提示，<b>绝不让页面无限转圈假死</b>。" },
        { term: "AbortController", en: "中止开关", plain: "浏览器用来<b>主动叫停</b>一个进行中的 fetch / 读取的开关。看门狗超时就用它 abort 掉读取。", match: ["AbortController"] },
        { term: "CRLF / LF", en: "换行符", plain: "SSE 事件围栏的两种写法：<code>\\n\\n</code>（LF，2 字节）和 <code>\\r\\n\\r\\n</code>（CRLF，4 字节）。代理改写后可能混用，切错位置会留游离字符。", match: ["\\r\\n\\r\\n", "\\n\\n"] },
        { term: "store / zustand", en: "前端状态仓库", plain: "前端的<b>统一数据仓库</b>（用 zustand 库）。所有事件解析出来都先写进它，组件再<b>各取所需</b>，不用各自订阅原始流。", match: ["zustand", "sse.ts", "handleEvent"] },
        { term: "stagger", en: "逐条动画", plain: "让时间轴<b>一段段长出来</b>、地图图钉逐个亮，给评委看清 Agent 排了哪几段；可一键跳过。" },
        { term: "marker", en: "地图标注", plain: "高德地图上代表每个地点的图钉。地图挂了就<b>降级成纯文字地点列表</b>，不白屏。" },
        { term: "jscode / proxy", en: "密钥与后端代理", plain: "高德地图要 <code>jscode</code> 安全密钥。直接写进前端会被 F12 看到，所以走<b>后端代理</b>：前端只指向 <code>/_AMapService</code>，后端转发时才注入密钥，<b>前端 0 暴露</b>。", match: ["NEXT_PUBLIC_AMAP_JS_CODE", "serviceHost=/_AMapService"] },
        { term: "decision_trace", en: "决策轨迹", plain: "Agent 一路想到哪一步的<b>记录</b>。用户中途取消时也<b>保留</b>它，让评委还能复盘。", match: ["decision_trace"] }
      ]
    },
    {
      group: "多人协作",
      items: [
        { term: "WebSocket (WS)", en: "双向长连接", plain: "浏览器和服务器之间的<b>双向</b>持久连接。多人协作房间靠它实时同步成员、约束、投票。", match: ["WS"] },
        { term: "asyncio.Lock", en: "房间锁", plain: "一把异步互斥锁。逼同一房间里的<b>多条约束串行处理</b>——B 必须等 A 处理完才进来，天然杜绝并发打架。", match: ["asyncio.Lock", "room.lock"] },
        { term: "cancel", en: "中断旧任务", plain: "新约束来了，先<b>掐掉算到一半的旧规划任务</b>、等它真正退出，再把约束合并成一段反馈<b>只复跑一次</b>。", match: ["planning_task.cancel()", "planning_aborted"] },
        { term: "snapshot", en: "全量快照", plain: "新人扫码加入时，把房间<b>当前全部状态</b>（成员/约束/投票/规划历史）整包推给他，让晚到的人<b>瞬间对齐到同一帧</b>。", match: ["get_state_snapshot()", "room_state"] },
        { term: "locked_stages", en: "锁段", plain: "被点赞<b>锁定</b>的行程段，重排时保留不动。点踩则反过来翻译成『换掉这段』的约束。", match: ["locked_stages"] }
      ]
    },
    {
      group: "范式 · 全局",
      items: [
        { term: "Plan-and-Execute", en: "规划-执行范式", plain: "Agent 范式之一：<b>先规划整盘、再逐步执行</b>（区别于走一步看一步）。本项目主干就是它。" },
        { term: "Routing", en: "路由范式", plain: "Agent 范式之一：<b>先分类、再分流</b>到对应处理器。对应输入模块的 router。" },
        { term: "Evaluator-Optimizer", en: "评判-优化范式", plain: "Agent 范式之一：<b>生成 → 评判 → 改进</b>的循环。对应这里的 planner 出方案、critic 评、replan 改。" },
        { term: "MiMo", en: "所用大模型", plain: "本项目用的 LLM。工程上<b>关掉了 thinking</b>（enable_thinking:False）以压低延迟。" },
        { term: "POI", en: "Point of Interest", plain: "地图上的『<b>兴趣点</b>』，也就是一个可去的地点（公园、展馆、咖啡馆……）。", match: ["POI"] },
        { term: "persona", en: "用户画像", plain: "累积下来的<b>用户偏好档案</b>，规划时作为先验参考；被反馈拒过的偏好会被压下去。" }
      ]
    }
  ];

  window.TOUR_GLOSSARY = DATA;

  /* ---- 建索引：精确文本 → 词条 ---- */
  var INDEX = {};
  var slugOf = {};
  function slug(s) { return "gloss-" + s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""); }
  DATA.forEach(function (sec) {
    sec.items.forEach(function (it) {
      it._id = slug(it.term);
      slugOf[it._id] = it;
      var keys = (it.match || []).concat([it.term]);
      keys.forEach(function (k) { if (!(k in INDEX)) INDEX[k] = it; });
    });
  });

  /* ---- DOM 构建 ---- */
  var drawer, backdrop, listEl, searchEl, handle, built = false;

  function elt(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function build() {
    if (built) return;
    built = true;

    handle = elt("button", "glossary-handle", '<span class="ico">📖</span><span>词汇表</span>');
    handle.setAttribute("aria-label", "打开词汇表");
    handle.addEventListener("click", function () { open(); });

    backdrop = elt("div", "glossary-backdrop");
    backdrop.addEventListener("click", close);

    drawer = elt("aside", "glossary-drawer");
    drawer.setAttribute("aria-hidden", "true");

    var head = elt("div", "glossary-head",
      '<div><h3>词汇表</h3><div class="sub">人话讲清每个术语 · ' + countItems() + ' 条</div></div>');
    var closeBtn = elt("button", "glossary-close", "✕");
    closeBtn.setAttribute("aria-label", "关闭");
    closeBtn.addEventListener("click", close);
    head.appendChild(closeBtn);

    searchEl = elt("input", "glossary-search");
    searchEl.type = "search";
    searchEl.placeholder = "搜索术语…  如 SSE、critic、看门狗";
    searchEl.addEventListener("input", function () { filter(searchEl.value.trim().toLowerCase()); });

    listEl = elt("div", "glossary-list");
    renderList();

    drawer.appendChild(head);
    drawer.appendChild(searchEl);
    drawer.appendChild(listEl);

    document.body.appendChild(handle);
    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);

    decorate(document.body);
    observe();
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && drawer.classList.contains("open")) close();
    });
  }

  function countItems() {
    var n = 0; DATA.forEach(function (s) { n += s.items.length; }); return n;
  }

  function renderList() {
    listEl.innerHTML = "";
    DATA.forEach(function (sec) {
      var secEl = elt("div", "gloss-sec");
      secEl.appendChild(elt("span", "gloss-sec-label", sec.group));
      sec.items.forEach(function (it) {
        var item = elt("div", "gloss-item");
        item.id = it._id;
        item.dataset.hay = (it.term + " " + (it.en || "") + " " + it.plain).toLowerCase();
        item.innerHTML =
          '<div><span class="gloss-term">' + it.term + "</span>" +
          (it.en ? '<span class="gloss-en">' + it.en + "</span>" : "") + "</div>" +
          '<p class="gloss-plain">' + it.plain + "</p>";
        secEl.appendChild(item);
      });
      listEl.appendChild(secEl);
    });
  }

  function filter(q) {
    var anyShown = false;
    DATA.forEach(function (sec) {
      var secEl = document.getElementById(sec.items[0]._id).parentNode;
      var secHit = false;
      sec.items.forEach(function (it) {
        var node = document.getElementById(it._id);
        var hit = !q || node.dataset.hay.indexOf(q) !== -1;
        node.style.display = hit ? "" : "none";
        if (hit) { secHit = true; anyShown = true; }
      });
      secEl.style.display = secHit ? "" : "none";
    });
    var empty = listEl.querySelector(".gloss-empty");
    if (!anyShown && !empty) listEl.appendChild(elt("div", "gloss-empty", "没有匹配的术语"));
    if (anyShown && empty) empty.remove();
  }

  function open(termId) {
    build();
    drawer.classList.add("open");
    backdrop.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    if (termId) {
      if (searchEl.value) { searchEl.value = ""; filter(""); }
      var node = document.getElementById(termId);
      if (node) {
        node.scrollIntoView({ block: "center", behavior: "smooth" });
        node.classList.remove("flash");
        void node.offsetWidth;          // 重启动画
        node.classList.add("flash");
      }
    }
  }

  function close() {
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
  }

  /* ---- 正文里命中术语的 <code>/<b> → 可点词条 ---- */
  function decorate(root) {
    if (!root || !root.querySelectorAll) return;
    var nodes = root.querySelectorAll("code, b");
    for (var i = 0; i < nodes.length; i++) {
      var c = nodes[i];
      if (c.classList.contains("gloss-on")) continue;
      if (c.closest(".glossary-drawer")) continue;
      var hit = INDEX[c.textContent.trim()];
      if (!hit) continue;
      c.classList.add("gloss-on");
      c.title = "词汇表：" + hit.term;
      (function (id) {
        c.addEventListener("click", function (e) { e.stopPropagation(); open(id); });
      })(hit._id);
    }
  }

  var pending = 0;
  function observe() {
    if (!window.MutationObserver) return;
    var mo = new MutationObserver(function (muts) {
      var touched = false;
      for (var i = 0; i < muts.length; i++) {
        if (muts[i].addedNodes && muts[i].addedNodes.length) { touched = true; break; }
      }
      if (!touched) return;
      if (pending) cancelAnimationFrame(pending);
      pending = requestAnimationFrame(function () { pending = 0; decorate(document.body); });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
