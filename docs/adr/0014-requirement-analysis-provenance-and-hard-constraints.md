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

1. **字段出处成为数据**(G-1):IntentExtraction 增出处标注——每约束字段标 `user_stated`/`prior`/`default` 之一;LLM 同次调用自报+规则层兜底校正(先验注入的字段 prompt 里已知,可机械回标)。两个立即消费方:narration 诚实段("按你说的带娃+距离你没提我按默认")、refiner 合并优先级(user_stated 绝不被 prior 覆盖)。降级链改读出处(先丢 default→prior,user_stated 最后且必须告知)归 G-2 一并做。
2. **硬软约束 schema 化**(G-2):受控词典按严重度分层(忌口/无障碍类=hard,风格/氛围类=soft;具体分法实现时对照 mock 数据逐词定);**hard tag 永不被 `relax_tag_search` 放宽**——宁可空候选走既有失败疏导,也不静默端上含牛肉的方案;soft 放宽必须产出 Advisory(新码 `CONSTRAINT_RELAXED`)接进 D-7 既有告知管线与 narration 诚实段,relaxed_tags 三条路径统一收口。physical_constraints(适老/无台阶)补 critic 事后复核(与 check_dietary 对称——安全攸关反而没门,不对称)。
3. **预算一等字段**(G-3):`budget_per_person: Optional[float]` 进 IntentExtraction+parser prompt 抽取规则;新增 check_budget critic(对照餐厅 avg_price/POI 票价);OVER_BUDGET 比较对象改为"本轮用户明说的数,缺省才退 persona 默认"。
4. **E-3 挂钩两项**(不在本 ADR 实施,记入 E-3 范围):`parse_confidence`/`ambiguous_fields` 接澄清消费(它们就是"该问什么"的字段级信号源,与 E-2-c 路由级低置信→澄清是两层互补);台账收编全局语义诉求(refiner/sniffer 识别出全局调整时补写 `node_ref=None` 条目,含同会话重申去重)——用户已确认产品预期:「我说过的忌口」应出现在台账面板。
5. **顺手清**(G-0):`pace_profile` 砍除(YAGNI,节奏由 pace_budget 从同伴/场景推导已是事实真相源;砍 prompt 段+清洗代码+refiner 特判);`gender_mix` 砍除;parser prompt「下游会回问澄清」措辞改为不承诺(E-3 落地后再改回)。

## 边界(不做)

完整 DST 信念追踪机器(demo 规模,refine 增量合并模型够用)/逐轮全history重抽取/槽位级评测基建(JGA/Slot F1,归 E-4 语料弧考虑)/多城市扩展语义。

## 备选与拒因

- **保持 flat list+调优先级常量**——拒:顶到底仍破防,硬约束语义靠"排序"表达不出"一票否决";且无出处数据时排序保护的对象是错的。
- **预算继续用「高人均」二元代理**——拒:演示场景明说数字,系统聋着;TravelPlanner 类基准均把预算列 hard。
- **pace_profile 接入 pace_budget(合流)**——拒(本期):需 D-5 式实测校准,成本不低;双轨里真正驱动算法的一侧已工作良好,抽取侧空转字段砍掉是更诚实的收口,未来需要用户显式节奏控制时再立项。
- **出处用独立 sidecar 结构**——拒:字段级 dict 标注随 intent 走,消费方一次拿全;sidecar 要跨模块传递,增加拼装点。

## 实施拆步(G 系列;TDD,每片独立 commit;排期=E-2-c 收口后、最终真 LLM 冒烟前)

- **G-0 顺手清**:pace_profile/gender_mix 砍除+prompt 措辞;grep gate 防悬空。
- **G-1 出处标注**:schema+parser/refiner prompt 自报+规则回标+narration/refiner 两消费方+测试(先验注入场景断言出处正确)。
- **G-2 硬软分层+破防告知**:词典严重度分层(逐词对照 mock 数据)+relax_tag_search 改造(hard 永不放宽)+CONSTRAINT_RELAXED Advisory 三路径收口+physical critic 复核+降级链按出处排序。
- **G-3 预算**:字段+抽取+check_budget+OVER_BUDGET 改比较对象;S1/S2 场景图级断言。
- 总验收:S1-S8 结构测试不回归;真 LLM 冒烟含"5 人局+明说预算+忌口降级到底"三个新探针场景;narration 出现出处口径与放宽告知的人话。

## 落地状态

⏳ 待实施(bug 修复批——氛围词漏传/桌型精确匹配守门——2026-07-03 已先行派出,不占 G 编号)。
