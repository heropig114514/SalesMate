/** 职责：提供四条固定日期行业资讯和推送契约测试夹具，均为虚构。
 * 实现：覆盖监管、产业、竞争、价格示例；不把固定发布时间改成当前时间。
 * 关联：共享语言/API 资源随需求界面统一版本；world-news.js 在近十四天窗口中展示，world-feed.js 校验字段与版本。
 * 目录：无函数或类。
 * 变量索引：DEMO_NEWS 为四条演示资讯；DEMO_PUSH 仅用于推送契约测试，不自动发送。 */
export const DEMO_NEWS = [
 {id:'singapore-packaging',version:1,demo:true,industry:'semiconductor',location:{name:'新加坡',latitude:1.35,longitude:103.82},published_at:'2026-09-19T09:20:00+08:00',title:'检测设备合规资料：出口前应核对的清单',summary:'演示情景：客户要求补充设备合规与技术资料。',body:['这是一条虚构的监管类资讯示例，不对应真实法规更新。','向客户提供资料前，应根据实际销售地区核对适用要求和官方来源。'],sources:[]},
 {id:'tokyo-optics',version:1,demo:true,industry:'optics',location:{name:'东京',latitude:35.68,longitude:139.69},published_at:'2026-09-18T08:00:00+08:00',title:'先进封装扩产，光学检测应用需求进入讨论',summary:'演示情景：制造企业计划评估新的检测产线。',body:['本条为虚构产业资讯。','示例客户关注产线节拍、微小缺陷检出与设备集成要求，真实判断需要核实企业公告。'],sources:[]},
 {id:'frankfurt-metrology',version:1,demo:true,industry:'metrology',location:{name:'法兰克福',latitude:50.11,longitude:8.68},published_at:'2026-09-16T09:00:00+08:00',title:'量测设备方案竞争：客户更关注交付与服务',summary:'演示情景：客户同时比较多个供应商的量测方案。',body:['本条为虚构竞争资讯，不指向真实企业。','可准备技术能力与服务范围对照表，所有参数应来自经核对的产品资料。'],sources:[]},
 {id:'sydney-industrial',version:1,demo:true,industry:'industrial',location:{name:'悉尼',latitude:-33.87,longitude:151.21},published_at:'2026-09-12T08:00:00+08:00',title:'关键零部件价格变化，报价前需重新确认成本',summary:'演示情景：供应商更新关键零部件的参考价格。',body:['本条为虚构价格资讯，不提供真实市场报价。','正式报价前应取得有效供应商报价，确认币种、交期与适用数量。'],sources:[]},
];
export const DEMO_PUSH = { ...DEMO_NEWS[3], id:'rotterdam-push-demo', version:1, title:'新的演示行业资讯', published_at:'2026-09-20T09:00:00+08:00' };
