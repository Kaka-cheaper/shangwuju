# ADR-0014 · 需求分析环节治理:字段出处、硬软约束与消费断链收口

- **状态**:Accepted(2026-07-03 · 约束抽取全链路审查 + 用户逐项拍板)
- **范围**:抽取侧(`agent/intent/parser.py`+prompt、refiner、soft_constraint_sniffer、`schemas/intent.py`/`schemas/tags.py`)+ 约束消费面(搜索过滤与降级 `tools/_helpers.py`、critic、narration 诚实段)+ 与台账/澄清(E-3)的衔接。承接 ADR-0011 决策 3(上下文打包器)、ADR-0013 决策 3(诉求台账)、[L0 响应义务契约](../L0-响应义务契约.md)禁令 2「绝不默默忽略」。
- **审查方法**:只读子代理全链路生产-消费对照(IntentExtraction 逐字段"谁写/谁读/读了改变什么")+ prior art 检索 + 主代理对最重声明的代码抽查实锤。

## 背景(2026-07-03 审查钉死)

**骨架成立**:categorical(三类受控词典+social_context 闭集)/non-categorical(companions.role 等自由文本)的槽位分层符合 schema-guided 抽取范式(Google SGD/DSTC8);首轮(raw_input+persona 先验)/反馈轮(intent+反馈+方案摘要+台账切片)的输入分层无信息断供;演示八场景走真实 parse_intent(canonical 短路只短路由不短抽取),冒烟证据可信;带错误回灌的校验-重试修复环是标准做法。

**管道漏水(按证据强度)**:
1. **系统里不存在真正的硬约束**:忌口级("不辣/无牛肉")与风格级("高人均/日料")混在同一 flat list,唯一区分是消费侧 `tools/_helpers.py` 私有常量 `_PRIORITY_TAGS_HIGH` 的放宽顺序——降级到底照样全丢、丢了零告知(`relaxed_tags` 只进 SSE 调试字段,三条规划路径全都拿到又全都丢弃,不进 narration)。经典 CSP/WCSP 的常识:硬约束=一票否决的过滤器,软约束=可加权违反,是两种语义;TravelPlanner 基准把预算/忌口类列为 hard constraint。
2. **出处在落地时被抹掉**:persona 先验拼进 prompt 融合输出,产出里"用户亲口说的"与"先验猜的"不可区分;降级链**原则上不可能**做到"保护用户说的、先丢系统猜的"——所需信息到不了那里。此为决策 1 与决策 2 的同链前后手。
3. **预算无槽位**:S1「预算别太贵」/S2「人均 50 左右」演示文案明说预算,IntentExtraction 无任何预算字段;既有 `AdvisoryCode.OVER_BUDGET` 比较的是 persona 静态默认值。
4. **休眠器官与双轨制**:`pace_profile` 抽取侧认真产出、规划侧 `pace_budget.py` 自证不读("本模块不读 pace_profile 字段",自己从 companions/social_context 重新推导);`parse_confidence` prompt 承诺"低置信下游会回问澄清"而下游无此分支(文档与实现不符);`gender_mix` 全仓零消费;台账 `NodeRef=None`(全局语义诉求)形态零写手——聊天里说的忌口永远不进台账面板。
5. **结构失配**(词汇治理后的下一个病灶层):`experience_tags` 餐厅搜索仅主路径漏传、party size 三路径三种算法且主路径"仅精确 2/4/6/8 才过滤"——已定性为 bug 即修,不入本 ADR 决策(修复批与本 ADR 同日派出)。

## 决策(用户 2026-07-03 拍板)

1. **字段出处成为数据**(G-1,二轮拷问后修订):IntentExtraction 增出处标注,枚举四值 `user_stated`/`inferred`/`prior`/`default`(二轮补 inferred——S7 类"安安静静"→"清净"标签源于用户的话但非字面,降级序位居中,narration 可说"我从『××』猜你想要…,不对可以说");**标量字段字段级、列表字段元素级**(键=值本身;一个 dietary 列表里"不辣"可能是用户说的、"日料"是先验注入的,字段级一个标签盖不住)。**出处键在词汇 canonical 化之后标**(K歌→KTV 再标,防悬空键)。首轮:LLM 自报+规则交叉校正(先验注入集已知,输出值∈先验集且原话没提→机械回标 prior;冲突规则赢);**反馈轮纯规则传播,不要 LLM 自报**——改动的字段/元素→user_stated,未动继承,撤回("算了不用不辣了")同步清理出处键,重申先验值→升级 user_stated。两个立即消费方:narration 诚实段、refiner 合并优先级(user_stated 绝不被 prior 覆盖)。降级链读出处归 G-2。
2. **硬软约束 schema 化**(G-2,二轮拷问后修订):受控词典按严重度分层(排除型忌口/无障碍类=hard,风格/氛围类=soft;physical 同样逐词分安全型/舒适型;实现时对照 mock 数据逐词定)。**hard×出处是 2×2 矩阵**:hard×user_stated 与 hard×prior **同级保护**(用户拍板:档案里的忌口也不能端牛肉,仅告知口径区分"按你说的/按你档案里的");soft×user_stated 丢必告知,soft×prior/default 先丢不打扰。降级全序:default→prior→inferred→user_stated(告知)→hard 永不。**告知机制改判(替换原"三路径收口"方案):出口满足度审计**——方案定稿处统一比对最终节点 vs 需求单每条约束,软约束未满足→生成 `CONSTRAINT_RELAXED` Advisory(复用 unmet_cuisines/unmet_pinned 同族模式:单点实现、不信中间层上报、天然覆盖三条路径),relaxed_tags 降级为纯调试信息。physical critic 事后复核补齐(与 check_dietary 对称)。**配套三件**:mock 数据完备性测试(每个 hard 标签≥N 家满足候选,防演示死路);hard 卡死空候选时 give_up 文案带"放宽建议"chips;多路告知合并去重后限额(≤2 条,措辞=自信取舍说明非道歉)。
3. **预算一等字段**(G-3,用户拍板×2):`budget_per_person: Optional[float]` 进 IntentExtraction+parser 抽取规则;**定量定性分轨**——"人均 50"进数字字段,"别太贵"**不硬映射数字**(系统不编造用户没说的话),留既有 tag 轨道靠出处标注让 narration 提及;新增 check_budget critic **判 SOFT**(advisory 告知"超了 X 元因为 Y 值得",不硬重排——mock 价格粒度粗,超一点全盘重排体验更差);OVER_BUDGET 比较对象改"本轮明说的数,缺省退 persona 默认"。前置核查:POI 票价字段 mock 可用性(不可用则先只管餐厅并声明)。诚实声明:G-3 完整解决 S2,S1 只解决"被听见"。
4. **E-3 挂钩两项**(不在本 ADR 实施,记入 E-3 范围):`parse_confidence`/`ambiguous_fields` 接澄清消费(它们就是"该问什么"的字段级信号源,与 E-2-c 路由级低置信→澄清是两层互补);台账收编全局语义诉求(refiner/sniffer 识别出全局调整时补写 `node_ref=None` 条目,含同会话重申去重)——用户已确认产品预期:「我说过的忌口」应出现在台账面板。
5. **顺手清**(G-0,二轮拷问后修订——发现暗雷):`pace_profile` 砍除**必须先迁移"太久了"收缩契约**——E-1 词表分拣保留"无聊/腻了/太久"族的理由就是钉着 refiner 节奏收缩,该收缩现写进无人读的 pace_profile(业务空转),裸砍=用户可见承诺静默死亡;正确动作:收缩目标迁到 `duration_hours`(有真实消费),探针验证"太久了"真的缩短行程后再砍字段(含 parser 4 条隐含规则/清洗防御/refiner 特判/feedback_detector 引用,grep 全消费方逐个清)。`gender_mix` 砍除;parser prompt「下游会回问澄清」措辞改为不承诺(E-3 落地后改回)。

## 边界(不做)

完整 DST 信念追踪机器(demo 规模,refine 增量合并模型够用)/逐轮全 history 重抽取/槽位级评测基建(JGA/Slot F1,归 E-4 语料弧考虑)/多城市扩展语义/**房间内出处记名**(记名是台账的职责,出处只到 user_stated 粒度,不重复建设)/**前端需求单面板出处徽标**(narration 承载即可,路演够用)/预算喂 weights(留观察)。

## 波及面清单(实施防踩空)

stub 固定 fixture 加新字段(否则全量 stub 测试当场炸)/前端 types.ts 镜像(intent_parsed 直达前端)/verify_schema_hardening 脚本/新字段一律 Optional+默认(redis 旧 checkpoint 免迁移)/G-1 与 G-3 同动 parser prompt 必须串行。

## 备选与拒因

- **保持 flat list+调优先级常量**——拒:顶到底仍破防,硬约束语义靠"排序"表达不出"一票否决";且无出处数据时排序保护的对象是错的。
- **预算继续用「高人均」二元代理**——拒:演示场景明说数字,系统聋着;TravelPlanner 类基准均把预算列 hard。
- **pace_profile 接入 pace_budget(合流)**——拒(本期):需 D-5 式实测校准,成本不低;双轨里真正驱动算法的一侧已工作良好,抽取侧空转字段砍掉是更诚实的收口,未来需要用户显式节奏控制时再立项。
- **出处用独立 sidecar 结构**——拒:字段级 dict 标注随 intent 走,消费方一次拿全;sidecar 要跨模块传递,增加拼装点。

## 实施拆步(G 系列;TDD,每片独立 commit;排期=E-2-c 收口后、最终真 LLM 冒烟前)

- **G-0 顺手清**:先迁"太久了"收缩契约到 duration_hours(探针先行)再砍 pace_profile;gender_mix 砍除;prompt 措辞。grep gate 防悬空。
- **G-1 出处标注**:schema(四值枚举/元素级)+parser 自报+规则交叉校正+refiner 纯规则传播(含撤回清理/升级)+narration/refiner 两消费方+测试(先验注入/撤回/升级场景断言)。
- **G-2 硬软分层+出口审计**:词典严重度分层(dietary+physical 逐词对照 mock)+relax_tag_search 改造(hard 永不放宽,含 2×2 矩阵降级序)+出口满足度审计(CONSTRAINT_RELAXED,unmet 同族单点——同时是"约束漏传"类 bug 的运行时天网)+physical critic 复核+mock 完备性测试+give_up 放宽建议 chips+告知限额+**结构对齐测试**(同一 intent 喂三条规划路径,断言搜索入参共享约束子集字段级相等——氛围词漏传类 bug 的静态网,不强扭代码结构)。
- **G-3 预算**:字段+定量抽取规则+check_budget(SOFT)+OVER_BUDGET 改比较对象+POI 票价预检;S2 图级断言+S1 被听见断言。
- **G-4 消费完备性 gate**(三轮拷问追加,2026-07-03 用户拍板——"有产无消"类 bug 的机器网,推广 ADR-0012 生命周期完备性测试先例):新测试文件三条断言——每个 SseEventType 在前端 event-handlers 有消费 case 或进"有意不消费"白名单(附理由,如 3 个 critic 闭环事件现状);IntentExtraction 每字段在 schema 外≥1 处读取或进白名单;AdvisoryCode 每码有生产点。本 ADR 挖出的十个"器官长了血管没接"缺陷(pace_profile/parse_confidence/ambiguous_fields/gender_mix/relaxed_tags/台账全局形态/3 SSE 事件/decision_trace/reject-modify/modifications)全属此类,gate 让此类 bug 出生当天即红。
- **实施纪律(三轮拷问定)**:同一概念多点消费一律"真相源声明"手法(单一解析函数+显式声明,venue_distance_km 先例)——G 系列内对 capacity 口径(含不含自己)、private_room 认定不对称顺手套用;比较点用枚举成员不用字符串字面量(emit_critic "critical" 漂移教训)。
- 总验收:S1-S8 结构测试不回归;真 LLM 冒烟含"5 人局+明说预算+忌口降级到底"三个新探针场景;narration 出现出处口径与放宽告知的人话。

## 落地状态

🔁 部分落地。bug 修复批(氛围词漏传/桌型守门)已落地 e70c6c1。
**G-0 已完成**(2026-07-03):"太久了"收缩契约迁移到 duration_hours(探针红转绿,用户可见效果兑现)+pace_profile/PaceProfile/gender_mix 全链砍除;超任务书排雷三处(personas.json 死键 extra=forbid 隐患/migrate_mock_v2 幂等复活步骤/verify_planning_quality 失效检查);redis 旧 checkpoint 兼容代价已声明接受(demo memory 模式)。1355 passed。
**G-4a 已完成**(2ce0341):SSE/Advisory 消费完备性 gate 三轴+白名单防腐(登记项被消费即红);IntentExtraction 字段轴留待 G-3 后(字段集仍在变)。
**G-1 已完成**(2026-07-03):field_provenance 四值/元素级(companions/preferred_poi_types 拍板排除);交叉校正规则赢;refiner 纯规则 diff 传播(变→user_stated/继承/重申升级/撤回清理,数字标量不做重申防假阳性);narration 诚实段真 LLM 实测口径兑现;打包器不透传(读码确认 provenance 随 intent 对象流转,三消费方不需字段级出处)。1394 passed。
**G-2 已完成**(2026-07-03):词典分层证据法(prompt 触发规则+mock 分布逐词定)/hard 永不放宽+出处降级序+tag_provenance 三路径透传/出口审计挂 finalize_plan(CONSTRAINT_RELAXED 三口径,default 不告知拍板)/告知限额 2+折叠/check_physical 新增+check_dietary 改判 hard-only ALL-match(主动修正)/give_up 死寂补洞(诚实文案+放宽 chips)/parity 静态网。联动真 bug×2: rule_planner 外层降级绕过 hard 保护已修;三引擎全败前端无反馈已修。mock 缺口登记: 无障碍 POI=0/无牛肉餐厅=0(待补数据)。1430 passed。
余 G-3→G-4b。
