#!/usr/bin/env python3
"""生成独立详情页 session.html —— 从场次列表点进去看单场完整诊断。

用法（被 export_site.py 调用）: python scripts/make_session_page.py <out_dir>
"""
import pathlib, sys

TPL = r"""<!doctype html><html lang=zh-CN><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<title>场次诊断 · 直播话术分析平台</title><style>
/* 与首页 dashboard.html 同一套令牌。
   这一页原来是深色优先、首页是浅色优先，同一个站点两种长相，
   从首页点进来像换了个系统。 */
@font-face{font-family:'DM Sans';font-style:normal;font-weight:400 700;font-display:swap;
  src:url('/hb/assets/fonts/DMSans.woff2') format('woff2')}
:root{--bg:#fafafa;--pnl:#fff;--pnl2:#f5f5f7;--fg:#0f172a;--dim:#64748b;
--line:#e7ebf1;--line2:#d3dae3;--acc:#3f6bff;--acc-soft:#eef3ff;
--ok:#059669;--ok-soft:#e7f6ef;--warn:#b45309;--warn-soft:#fdf0db;
--bad:#dc2626;--bad-soft:#fef2f2;
--r-sm:8px;--r-md:12px;--r-lg:16px;--r-pill:999px;
--sh-xs:0 1px 2px #0f172a0d;--sh-sm:0 1px 3px #0f172a0f,0 1px 2px #0f172a0a;
--mono:'JetBrains Mono','SF Mono',ui-monospace,Consolas,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#0c0e13;--pnl:#151922;--pnl2:#1b212c;
--fg:#e9edf4;--dim:#8992a3;--line:#242c39;--line2:#333d4d;--acc:#5b93ff;--acc-soft:#16233d;
--ok:#2fcf94;--ok-soft:#10291f;--warn:#f0aa46;--warn-soft:#2c2113;
--bad:#ff6b6b;--bad-soft:#2e1618;
--sh-xs:0 1px 2px #0006;--sh-sm:0 1px 3px #0008}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;
font:14px/1.65 'DM Sans',-apple-system,'PingFang SC','Hiragino Sans GB',
"Microsoft YaHei",system-ui,sans-serif;padding:0 0 50px}
h1,h2,h3{letter-spacing:-.02em}
.bar{position:sticky;top:0;z-index:9;background:var(--pnl);border-bottom:1px solid var(--line);
padding:11px 20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.bar h1{font-size:15px;margin:0;font-weight:650}
.bar a{color:var(--acc);text-decoration:none;font-size:12.5px}
.wrap{max-width:1560px;margin:0 auto;padding:16px 20px}
.cd{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:9px;margin-bottom:14px}
.cd div{background:var(--pnl);border:1px solid var(--line);border-radius:var(--r-md);
  padding:12px 14px;box-shadow:var(--sh-xs)}
.cd i{display:block;font-size:10.5px;color:var(--dim);font-style:normal}
.cd b{font-size:16px;font-family:var(--mono);font-weight:600}
.cd small{font-size:11px;color:var(--dim);font-weight:400}
.tabs{display:flex;gap:4px;margin:6px 0 13px;flex-wrap:wrap}
.tab{padding:7px 15px;border-radius:var(--r-pill);background:var(--pnl);border:1px solid var(--line);
cursor:pointer;font-size:12.5px}
.tab.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.tab.dis{opacity:.42;cursor:not-allowed}
.tab em{font-style:normal;color:var(--dim);font-size:11px}
.tab.on em{color:#dbe7ff}
.grid{display:grid;grid-template-columns:1fr 340px 280px;gap:12px;align-items:start}
@media(max-width:1240px){.grid{grid-template-columns:1fr 320px}}
@media(max-width:880px){.grid{grid-template-columns:1fr}}
.pane{background:var(--pnl);border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--sh-xs);
display:flex;flex-direction:column;overflow:hidden}
.ph{flex:none;font-size:11px;color:var(--dim);font-weight:650;padding:9px 12px;
border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
.ph em{font-style:normal;font-weight:400;font-family:var(--mono)}
.pc{padding:11px 13px;overflow:auto}
.bt{font-size:11.5px;color:var(--dim);font-weight:650;margin:0 0 8px}
.flow{max-height:70vh;overflow:auto}
.seg{padding:9px 0;border-bottom:1px solid var(--line);display:flex;gap:11px}
.seg:last-child{border:none}
.fshot{width:126px;border-radius:var(--r-sm);flex:none;cursor:pointer;background:var(--pnl2);
align-self:flex-start}
.fbody{flex:1;min-width:0}
.tm{color:var(--acc);font-size:11px;font-family:var(--mono);margin-right:8px}
.tx{font-size:13.5px;line-height:1.72}
.tag{display:inline-block;padding:2px 8px;border-radius:var(--r-sm);font-size:11px;font-weight:500;
background:rgba(77,141,255,.14);color:var(--acc);margin:3px 4px 0 0}
.tag.p{background:rgba(46,204,143,.15);color:var(--ok)}
.tag.s{background:rgba(240,168,60,.16);color:var(--warn)}
.tag.risk{background:rgba(255,95,95,.16);color:var(--bad)}
.re{display:block;font-size:10.5px;color:var(--ok);margin-bottom:3px}
.alert{background:rgba(255,95,95,.09);border:1px solid rgba(255,95,95,.3);
border-radius:8px;padding:9px 11px;margin-top:8px;font-size:12px}
.alert b{color:var(--bad)}
.empty{color:var(--dim);font-size:12.5px;padding:16px 0;text-align:center}
.row{display:flex;align-items:center;gap:8px;margin:6px 0}
.row .lb{width:52px;font-size:11.5px;color:var(--dim);flex:none;text-align:right}
.row .tr{flex:1;background:var(--pnl2);border-radius:3px;height:12px;overflow:hidden}
.row .fl{height:100%;background:var(--acc)}
.row .nm{width:38px;font-size:11px;color:var(--dim);flex:none;font-family:var(--mono)}
.miss{border-left:2px solid var(--warn);padding:6px 0 6px 10px;margin:9px 0}
.miss .ex{color:var(--dim);font-size:11.5px;margin-top:2px}
.badge{display:inline-block;padding:2px 9px;border-radius:var(--r-pill);font-size:11px;font-weight:500;
background:var(--pnl2);color:var(--dim)}
.dm div{padding:4px 0;border-bottom:1px solid var(--line);font-size:12.5px}
.nick{color:var(--dim);margin-right:5px}
.rlist{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px}
.rm2{background:var(--pnl);border:1px solid var(--line);border-radius:var(--r-md);
  padding:13px 15px;box-shadow:var(--sh-xs)}
.vrow{cursor:pointer}.vrow:hover .lb{color:var(--acc)}
.vmark{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:var(--r-sm);
  font-size:10.5px;background:var(--acc-soft);color:var(--acc);vertical-align:1px}
#vex img{width:100%;border-radius:var(--r-md);display:block;cursor:zoom-in;background:var(--pnl2)}
.rm2.off{opacity:.62}
.rm2 .rh{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.rm2 .ri{font-weight:680;font-size:13px;color:var(--acc);font-family:var(--mono)}
.rm2 .rp{font-weight:600;font-size:13px;flex:1}
.rm2 .rt{font-size:11px;color:var(--dim);font-family:var(--mono)}
.rm2 .rs{font-size:12px;color:var(--dim);margin:6px 0 7px;line-height:1.6}
.rm2 .rf{font-size:11.5px;display:flex;gap:9px;flex-wrap:wrap;
border-top:1px solid var(--line);padding-top:7px}
.big{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;align-items:center;
justify-content:center;z-index:50;cursor:zoom-out}
.big.on{display:flex}
.big img{max-width:94vw;max-height:94vh;border-radius:8px}
</style>
<div class=bar>
  <h1 id=title>场次诊断</h1>
  <span id=meta style="font-size:12.5px;color:var(--dim)"></span>
  <span style="flex:1"></span>
  <span id="rep"></span>
  <a href="./">&larr; 返回总览</a>
</div>
<div class=wrap>
  <div class=cd id=cards></div>
  <div class=tabs id=tabs></div>
  <div id=main></div>
</div>
<div class=big id=big onclick="this.classList.remove('on')"><img id=bigimg></div>
<script>
const $=i=>document.getElementById(i);
const CLS={product:'p',sales:'s',interact:'',risk:'risk'};
let S=null, tab='overview';
function mmss(s){s=Math.max(0,Math.floor(s||0));
  return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}
function dur(s){s=Math.floor(s||0);if(!s)return '—';const h=Math.floor(s/3600),m=Math.floor(s%3600/60);
  return h?(h+' 小时 '+m+' 分'):(m+' 分钟')}
function esc(s){return String(s==null?'':s).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}
function bigPic(src){$('bigimg').src=src;$('big').classList.add('on')}
function frameUrl(sec){const s=S;if(!s||!s.frame_secs||!s.frame_secs.length)return '';
  let b=s.frame_secs[0];for(const x of s.frame_secs){if(Math.abs(x-sec)<Math.abs(b-sec))b=x}
  return './'+s.path+'/thumbs/'+String(b).padStart(6,'0')+'.jpg'}
async function boot(){
  const p=new URLSearchParams(location.search).get('p');
  if(!p){$('main').innerHTML='<div class=empty>缺少场次参数</div>';return}
  const f=p.replace(/\//g,'_')+'.json';
  try{
    const r=await fetch('./sessiondata/'+f);
    if(!r.ok)throw new Error('HTTP '+r.status);
    const t=await r.text();
    if(t.trimStart().charAt(0)==='<')throw new Error('BADPATH');
    S=JSON.parse(t);
  }catch(e){
    $('main').innerHTML='<div class=empty>'+
      (String(e.message)==='BADPATH'?'数据没取到，链接可能不对':'读取场次数据失败')+
      '</div>';return }
  const T=document.title=S.name+' · 场次诊断';
  $('title').textContent=S.name;
  $('meta').innerHTML=esc(S.category)+' ・ '+esc(S.started.replace('_',' '))+' ・ '+
    dur(S.elapsed);
  if(S.has_report)$('rep').innerHTML='<a href="./'+S.path+'/report.html" target=_blank>完整报告 &#8599;</a>';
  const m=S.metrics||{};
  const cds=[['时长',mmss(S.elapsed)],['转写',(S.transcript_chars||S.chars)+'<small> 字</small>'],
    ['语速',(m['语速_字每分']||'—')+'<small> 字/分</small>'],['弹幕',S.danmu],
    ['其中刷屏',(S.spam_total||0)+'<small> 条</small>'],
    ['行动指令',(m['行动指令_次每分']!=null?m['行动指令_次每分']:'—')+'<small> 次/分</small>'],
    ['购买意向',m['购买意向弹幕']!=null?m['购买意向弹幕']:'—'],
    ['最长无逼单',(m['最长无逼单间隔_秒']!=null?m['最长无逼单间隔_秒']:'—')+'<small> 秒</small>']];
  $('cards').innerHTML=cds.map(x=>'<div><i>'+x[0]+'</i><b>'+x[1]+'</b></div>').join('');
  renderTabs(); render();
}
function renderTabs(){
  const dp=S.deep||{};
  const TB=[['overview','概览',true],
            ['rounds','分轮诊断',!!(S.rounds&&S.rounds.length)],
            ['script','讲解逻辑',!!dp.script],
            ['audience','观众洞察',!!dp.audience],
            ['visual','画面道具',!!dp.visual],
            ['vmetrics','视觉量化',!!(S.visual_metrics&&S.visual_metrics.n_frames)],
            ['tmetrics','话术量化',!!(S.talk&&S.talk.groups)]];
  if(!TB.find(x=>x[0]===tab&&x[2]))tab='overview';
  $('tabs').innerHTML=TB.map(x=>'<div class="tab '+(tab===x[0]?'on':'')+(x[2]?'':' dis')+
    '"'+(x[2]?' onclick="tab=\''+x[0]+'\';renderTabs();render()"':'')+'>'+x[1]+
    (x[2]?'':' <em>·未分析</em>')+'</div>').join('');
}
function render(){
  if(tab==='rounds')return void($('main').innerHTML=vRounds());
  if(tab==='script')return void($('main').innerHTML=vScript());
  if(tab==='audience')return void($('main').innerHTML=vAudience());
  if(tab==='visual')return void($('main').innerHTML=vVisual());
  if(tab==='vmetrics')return void($('main').innerHTML=vVMetrics());
  if(tab==='tmetrics')return void($('main').innerHTML=vTMetrics());
  $('main').innerHTML=vOverview();
}
function vOverview(){
  const m=S.metrics||{};
  const flow=S.flow.length?S.flow.map(f=>'<div class=seg>'+
    '<img class=fshot loading=lazy src="'+frameUrl(f.start)+'" onclick="bigPic(this.src)" '+
    'onerror="this.style.display=\'none\'">'+
    '<div class=fbody>'+(f.danmu_after?('<span class=re>弹幕 '+f.danmu_after+
      (f.buy_after?' · 购买'+f.buy_after:'')+'</span>'):'')+
    '<span class=tm>'+mmss(f.start)+'</span><span class=tx>'+esc(f.text).slice(0,180)+'</span>'+
    '<div>'+f.tags.map(t=>'<span class="tag '+(CLS[t.dim]||'')+'">'+esc(t.name)+'</span>').join('')+
    '</div></div></div>').join(''):'<div class=empty>暂无转写</div>';
  const dm=(S.danmu_real||[]).slice(-30).reverse().map(x=>'<div><span class=nick>'+esc(x.nick)+
    '</span>'+esc(x.text)+'</div>').join('')||'<div class=empty>暂无真人弹幕</div>';
  const spam=(S.danmu_spam||[]).length?('<div class=bt style="margin-top:11px">中控刷屏（已折叠）</div>'+
    S.danmu_spam.map(s=>'<div class=row><div class=lb style=width:44px>×'+s.n+'</div><div style="flex:1;font-size:12px;color:var(--dim)">'+esc(s.text)+'</div></div>').join('')):'';
  const dims=(S.dims||[]).map(d=>'<div class=row><div class=lb>'+esc(d.name)+
    '</div><div class=tr><div class=fl style="width:'+(d.total?d.used/d.total*100:0)+'%"></div></div>'+
    '<div class=nm>'+d.used+'/'+d.total+'</div></div>').join('');
  const top=(S.top||[]).map(t=>'<div class=row><div class=lb style=width:62px>'+esc(t.name)+
    '</div><div class=tr><div class=fl style="width:'+(S.top[0].n?t.n/S.top[0].n*100:0)+'%"></div></div>'+
    '<div class=nm>'+t.n+'</div></div>').join('');
  const risk=(S.risk||[]).map(r=>'<div class=alert><b>&#9888; '+esc(r.name)+'</b> <span class=badge>'+
    esc(r.src||'')+'</span><div style=margin-top:3px><span class=tm>'+mmss(r.t)+'</span>'+
    esc(r.text)+'</div></div>').join('');
  return '<div class=grid><div class=pane><div class=ph><span>话术时间轴</span><em>'+
    S.flow.length+' 段</em></div><div class="pc flow">'+flow+'</div></div>'+
    '<div><div class=pane style=margin-bottom:12px><div class=ph><span>观众弹幕</span></div>'+
    '<div class=pc><div class=dm>'+dm+'</div>'+spam+'</div></div></div>'+
    '<div><div class=pane style=margin-bottom:12px><div class=ph>能力维度覆盖</div><div class=pc>'+
    (dims||'<div class=empty>暂无</div>')+'</div><div class=ph><span>话术标签分布</span></div>'+
    '<div class=pc>'+(top||'<div class=empty>暂无</div>')+'</div></div>'+
    (risk?('<div class=pane><div class=ph>风险话术</div><div class=pc>'+risk+'</div></div>'):'')+
    '</div></div>';
}
function vScript(){
  const d=(S.deep||{}).script||{}, c=d.core_point||{};
  const st=d.structure||{};
  const chains=(d.logic_chains||[]).map(x=>'<div class=miss><div><b>'+esc(x.point||'')+
    '</b> <span class=tag>'+esc(x.method||'')+'</span></div><div style="margin:3px 0;font-size:12.5px">'+
    esc(x.how||'')+'</div><div class=ex><span class=tm>'+mmss(x.t)+'</span>'+esc(x.quote||'')+'</div></div>').join('');
  const hl=(d.highlights||[]).map(x=>'<div class=miss style="border-left-color:var(--ok)">'+
    '<div class=ex><span class=tm>'+mmss(x.t)+'</span>'+esc(x.quote||'')+'</div>'+
    '<div class=ex>好在：'+esc(x.why||'')+'</div></div>').join('');
  const iss=(d.issues||[]).map(x=>'<div class=miss><div>'+esc(x.problem||'')+'</div>'+
    '<div class=ex>改法：'+esc(x.fix||'')+'</div></div>').join('');
  const flow=(st.flow||[]).map((x,i)=>'<div style="padding:4px 0;font-size:12.5px">'+
    '<b style="color:var(--acc);font-family:var(--mono)">'+(i+1)+'</b> '+esc(x)+'</div>').join('');
  return '<div class=grid><div class=pane><div class=ph><span>讲解结构</span><em>'+
    (st.flow||[]).length+' 步</em></div><div class=pc>'+
    '<div class=bt>开场</div><div style="font-size:12.5px">'+esc(st.opening||'')+'</div>'+
    '<div class=bt style="margin-top:11px">推进环节</div>'+(flow||'<div class=empty>暂无</div>')+
    '<div class=bt style="margin-top:11px">收口</div><div style="font-size:12.5px">'+esc(st.closing||'')+'</div>'+
    '<div class=bt style="margin-top:11px">讲解循环</div><div style="font-size:12.5px">'+esc(st.cycle||'')+'</div>'+
    '<div class=alert style="background:rgba(46,204,143,.08);border-color:rgba(46,204,143,.3)">'+
    '<b style="color:var(--ok)">核心卖点：'+esc(c.what||'')+'</b> 重复 '+esc(c.repeat||0)+
    ' 次 ・ '+esc(c.judge||'')+'</div></div></div>'+
    '<div class=pane><div class=ph><span>逻辑链</span><em>'+((d.logic_chains||[]).length)+
    '</em></div><div class=pc>'+(chains||'<div class=empty>暂无</div>')+'</div></div>'+
    '<div><div class=pane style=margin-bottom:12px><div class=ph>亮点</div><div class=pc>'+
    (hl||'<div class=empty>暂无</div>')+'</div></div>'+
    '<div class=pane><div class=ph>问题</div><div class=pc>'+(iss||'<div class=empty>暂无</div>')+
    '</div></div></div></div>';
}
function vAudience(){
  const a=(S.deep||{}).audience||{};
  const cs=(a.concerns||[]).map(x=>'<div class=miss><div style="font-size:12.5px"><b>'+esc(x.topic||'')+
    '</b> <span class=badge>'+x.n+' 条</span> <span class=badge style="color:'+
    (x.answered?'var(--ok)':'var(--bad)')+'">'+(x.answered?'已回应':'未回应')+'</span></div>'+
    (x.samples||[]).map(s=>'<div class=ex>'+esc(s)+'</div>').join('')+'</div>').join('');
  return '<div class=grid><div class=pane><div class=ph><span>观众关注点</span><em>'+
    ((a.concerns||[]).length)+' 项</em></div><div class=pc>'+(cs||'<div class=empty>暂无</div>')+
    '</div></div><div class=pane><div class=ph>整体判断</div><div class=pc>'+
    '<div style="font-size:13px;line-height:1.8">'+esc(a.summary||a.comment||'')+'</div></div></div></div>';
}
function vVisual(){
  const v=(S.deep||{}).visual||{};
  const props=(v.props||[]).map(x=>'<div class=miss><div style="font-size:12.5px"><b>'+esc(x.name||'')+
    '</b></div><div class=ex>'+esc(x.usage||x.use||x.note||'')+'</div></div>').join('');
  const tips=(v.techniques||v.tips||[]).map(x=>'<div style="padding:4px 0;font-size:12.5px">• '+esc(x)+'</div>').join('');
  const an=v.anchor||{}, fit=an.fit||{};
  const fitC={'一致':'var(--ok,#1e7f55)','不搭':'var(--bad,#9b3a3a)'}[fit.verdict]||'var(--dim)';
  const look=(an.look||fit.verdict)?'<div style="font-size:13px;line-height:1.8;margin-top:8px">'+
    (fit.verdict?'<b style="color:'+fitC+'">造型适配（标准 3.1）：'+esc(fit.verdict)+'</b> '+esc(fit.reason||'')+'<br>':'')+
    esc(an.look||'')+(an.changes?'<br>'+esc(an.changes):'')+'</div>':'';
  return '<div class=grid><div class=pane><div class=ph>场景与服化道</div><div class=pc>'+
    '<div style="font-size:13px;line-height:1.8">'+esc(v.scene||v.setting||'')+'</div>'+look+'</div></div>'+
    '<div class=pane><div class=ph><span>道具与展示</span><em>'+((v.props||[]).length)+
    '</em></div><div class=pc>'+(props||'<div class=empty>暂无</div>')+'</div></div>'+
    '<div class=pane><div class=ph>可借鉴技巧</div><div class=pc>'+(tips||'<div class=empty>暂无</div>')+
    '</div></div></div>';
}
function showVex(field,val){
  const V=S.visual_metrics||{};
  const fr=((V.examples||{})[field]||{})[val];
  if(!fr||!fr.length){alert('暂无案例帧');return}
  const F={'makeup.level':'妆容强度','makeup.hair':'发型','makeup.lip':'唇色',
    'outfit.type':'服装款式','outfit.color':'服装主色','outfit.style':'风格',
    'outfit.badge':'工牌/胸针','staging.shot':'景别','staging.hpos':'水平位置',
    'staging.product':'与产品关系','staging.gaze':'视线','staging.ratio':'人物占比',
    'scene.bg':'背景类型','scene.tone':'主色调','scene.density':'信息密度',
    'scene.light':'灯光','scene.price_shown':'价格展示'};
  const imgs=fr.map(f=>'<div style="text-align:center">'+
    '<img src="./'+S.path+'/thumbs/'+String(f.sec).padStart(6,'0')+'.jpg" '+
    'onclick="bigPic(this.src)">'+
    '<div class=ex style="margin-top:5px">'+mmss(f.sec)+'</div></div>').join('');
  const el=document.createElement('div');
  el.id='vex';
  el.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:60;'+
    'display:flex;align-items:center;justify-content:center';
  el.innerHTML='<div style="background:var(--pnl);border:1px solid var(--line);border-radius:12px;'+
    'padding:16px;max-width:min(92vw,900px);max-height:88vh;overflow:auto">'+
    '<div style="display:flex;justify-content:space-between;margin-bottom:12px">'+
    '<b>'+esc((F[field]||field)+' · '+val)+'</b>'+
    '<span onclick="this.closest(&#39;#vex&#39;).remove()" style="cursor:pointer;color:var(--dim);'+
    'font-size:18px">&times;</span></div>'+
    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">'+
    imgs+'</div></div>';
  el.onclick=e=>{if(e.target===el)el.remove()};
  document.body.appendChild(el);
}
function vVMetrics(){
  const V=S.visual_metrics||{};
  const dist=V.dist||{};
  const F={'makeup.level':'妆容强度','makeup.hair':'发型','outfit.type':'服装款式',
    'outfit.color':'服装主色','outfit.style':'风格','outfit.badge':'工牌/胸针',
    'staging.shot':'景别','staging.hpos':'水平位置','staging.product':'与产品关系',
    'staging.gaze':'视线','staging.ratio':'人物占比','scene.bg':'背景类型',
    'scene.tone':'主色调','scene.density':'信息密度','scene.light':'灯光',
    'scene.price_shown':'价格展示'};
  const EX=V.examples||{};
  const bar=k=>{const a=(dist[k]||[]).slice(0,5);if(!a.length)return '';
    const top=a[0].pct||1;
    return '<div class=bt style="margin-top:11px">'+esc(F[k]||k)+'</div>'+
      a.map(x=>{
        const has=(EX[k]||{})[x.v];
        const tag=String(x.v).replace(/['\\\\]/g,"");
        return '<div class=row'+(has?' vrow':'')+'"'+(has?
          ' onclick="showVex(\''+k+'\',\''+tag+'\')"':'')+'>'+
          '<div class=lb style="width:74px;text-align:left">'+esc(x.v)+
          (has?'<span class=vmark>案例</span>':'')+'</div>'+
          '<div class=tr><div class=fl style="width:'+Math.round(x.pct/top*100)+'%"></div></div>'+
          '<div class=nm>'+x.pct+'%</div></div>';
      }).join('')};
  const D2=V.derived||{};
  const dRows=Object.keys(D2).map(k=>{const x=D2[k];
    const val=x.value!=null?x.value:(x.pct!=null?x.pct+'%':(x.main||''));
    return '<div style="display:flex;justify-content:space-between;padding:6px 0;'+
      'border-bottom:1px solid var(--line)"><span style="font-size:12.5px">'+esc(k)+
      (x.desc?'<div class=ex>'+esc(x.desc)+'</div>':'')+'</span>'+
      '<b style="font-family:var(--mono)">'+esc(String(val))+'</b></div>'}).join('');
  const props=(V.props||[]).map(x=>'<div class=row><div class=lb style="width:76px;text-align:left">'+
    esc(x.v)+'</div><div class=tr><div class=fl style="width:'+x.pct+
    '%;background:var(--warn)"></div></div><div class=nm>'+x.pct+'%</div></div>').join('');
  return '<div class=bt>本场抽 '+V.n_frames+' 帧逐帧标注（全场 '+
    (S.frame_secs||[]).length+' 张截图中均匀抽样）</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:11px;align-items:start">'+
    '<div class=pane><div class=ph><span>妆造与服装</span></div><div class=pc>'+
    bar('makeup.level')+bar('makeup.hair')+bar('outfit.type')+bar('outfit.color')+
    bar('outfit.style')+'</div></div>'+
    '<div class=pane><div class=ph><span>站位与镜头</span></div><div class=pc>'+
    bar('staging.shot')+bar('staging.hpos')+bar('staging.product')+bar('staging.gaze')+
    bar('staging.ratio')+'</div></div>'+
    '<div><div class=pane style="margin-bottom:11px"><div class=ph><span>场景</span></div>'+
    '<div class=pc>'+bar('scene.bg')+bar('scene.tone')+bar('scene.density')+
    bar('scene.light')+bar('scene.price_shown')+'</div></div>'+
    '<div class=pane><div class=ph><span>道具使用率</span></div><div class=pc>'+
    (props||'<div class=empty>暂无</div>')+'</div></div></div></div>'+
    '<div class=pane style="margin-top:11px"><div class=ph>本场量化指标</div><div class=pc>'+
    dRows+'</div></div>';
}
function vTMetrics(){
  const T=S.talk||{}, g=T.groups||{};
  const seg=(p,c)=>(p>0?('<div style="width:'+p+'%;background:'+c+';height:100%;float:left;'+
    'font-size:11px;color:#fff;line-height:22px;text-align:center;overflow:hidden;'+
    'white-space:nowrap">'+(p>=8?p+'%':'')+'</div>'):'');
  const order=[['塑品','#4d8dff'],['逼单','#f0a83c'],['互动','#2ecc8f']];
  const bar='<div style="height:22px;background:var(--pnl2);border-radius:4px;overflow:hidden;'+
    'margin:8px 0">'+order.map(([k,c])=>seg((g[k]||{}).pct||0,c)).join('')+
    seg(T.other?T.other.pct:0,'#5a6270')+'</div>';
  const cards=order.map(([k,c])=>{
    const x=g[k]||{};
    return '<div style="flex:1;background:var(--pnl2);border-radius:8px;padding:10px 12px">'+
      '<div style="font-size:11.5px;color:'+c+';font-weight:650">'+x.name+'</div>'+
      '<div style="font-size:20px;font-family:var(--mono);margin:3px 0">'+(x.pct||0)+'%</div>'+
      '<div class=ex>'+Math.round((x.sec||0)/60)+' 分钟 ・ '+x.segments+' 段 ・ '+
      x.per_min+' 次/分</div>'+
      '<div class=ex>单次均长 '+Math.round(x.avg_span_sec||0)+' 秒 ・ 覆盖 '+
      (x.coverage_decile||0)+'/10 段</div>'+
      ((x.top||[]).length?'<div class=ex style="margin-top:4px">常用：'+
        x.top.slice(0,3).map(t=>esc(t.name)+'×'+t.n).join('、')+'</div>':'')+'</div>'}).join('');
  const pg=T.push_gap;
  const gap=pg?('<div class=pane style="margin-top:11px"><div class=ph><span>促单空窗</span>'+
    '<em>连续没提促单的最长时间</em></div><div class=pc>'+
    '<div style="font-size:13px">最长 <b style="font-family:var(--mono);font-size:17px">'+
    Math.round(pg.max_sec)+'</b> 秒，出现在 '+mmss(pg.max_at)+'</div>'+
    '<div class=ex style="margin-top:6px">超 60 秒的空窗 '+pg.over_60s+' 次'+
    (pg.over_60s?'（观众想买时没人推）':'（促单密度很好）')+'</div>'+
    (pg.worst||[]).slice(0,3).map(w=>'<div class=ex>'+mmss(w.start)+' 起，空窗 '+
      Math.round(w.sec)+' 秒</div>').join('')+'</div></div>'):'';
  return '<div class=bt>按时间轴切出三类话术，一段含多类时按时长均分</div>'+
    bar+'<div style="display:flex;gap:11px">'+cards+'</div>'+gap+
    '<div class=ex style="margin-top:9px">读法：逼单占比高=促单密；互动占比高=陪聊多。'+
    '标杆同品类均值请到首页「话术量化」页对比</div>';
}
function vRounds(){
  const rs=S.rounds||[], done={};
  (S.round_analysis||[]).forEach(x=>{done[x.idx]=x});
  const rows=rs.map(r=>{const a=done[r.idx];
    const flags=[r.has_price?'价':'',r.has_urgency?'急':'',r.has_demo?'演':''].filter(Boolean).join(' ');
    const _s=v=>v==null?'—':v;  // 标准里没有该项规则时模型会填 null，别显示成 null
    const sc=a&&a.score?('<b>'+_s(a.score.product)+'/'+_s(a.score.sales)+'/'+_s(a.score.interact)+'/'+_s(a.score.pace)+'</b>'):'—';
    const miss=a&&a.structure&&a.structure.missing&&a.structure.missing.length?
      ('<span class=badge style="color:var(--warn)">缺 '+esc(a.structure.missing.join('、'))+'</span>'):'';
    return '<div class=rm2'+(a?'':' off')+(a?' onclick="showRound('+r.idx+')" style=cursor:pointer':'')+'>'+
      '<div class=rh><span class=ri>第 '+r.idx+' 轮</span><span class=rp>'+esc(r.product||'未标注')+
      '</span><span class=rt>'+mmss(r.start)+'-'+mmss(r.end)+' · '+mmss(r.dur)+'</span></div>'+
      '<div class=rs>'+esc(r.summary||'')+'</div>'+
      '<div class=rf><span style="color:var(--dim)">'+(flags?'手法 '+flags:'')+
      '</span><span style="color:var(--dim)">评分(产/销/互/节)</span> '+sc+' '+miss+
      (a?'':' <span class=badge>未单轮分析</span>')+'</div></div>';}).join('');
  return '<div class=bt>分轮诊断 · '+rs.length+' 轮（点已分析的轮看详情）</div><div class=rlist>'+rows+'</div>';
}
function showRound(i){
  const rd=(S.round_analysis||[]).find(x=>x.idx===i); if(!rd)return;
  const st=rd.structure||{}, sc=rd.score||{};
  const bars=[['product','产品力'],['sales','销售力'],['interact','互动力'],['pace','节奏力']]
    .map(([k,n])=>{const v=parseInt(sc[k]||0);return '<div class=row><div class=lb>'+n+
      '</div><div class=tr><div class=fl style="width:'+(v*20)+'%"></div></div>'+
      '<div class=nm>'+v+'/5</div></div>'}).join('');
  const chains=(rd.logic_chains||[]).map(c=>'<div class=miss><div style="font-size:12.5px"><b>'+
    esc(c.point||'')+'</b> <span class=tag>'+esc(c.method||'')+'</span></div>'+
    '<div class=ex>'+esc(c.how||'')+'</div>'+(c.quote?'<div class=ex><span class=tm>'+
    mmss(c.t<0?0:c.t)+'</span>'+esc(c.quote)+'</div>':'')+'</div>').join('');
  const probs=(rd.problems||[]).map(p=>'<div class=miss><div><span class=badge style="color:'+
    (p.severity==='高'?'var(--bad)':p.severity==='中'?'var(--warn)':'var(--dim)')+'">'+
    esc(p.severity||'')+'</span> '+esc(p.problem||'')+'</div><div class=ex>改法：'+esc(p.fix||'')+
    '</div></div>').join('');
  const hl=(rd.highlights||[]).map(h=>'<div class=miss style="border-left-color:var(--ok)">'+
    '<div class=ex><span class=tm>'+mmss(h.t<0?0:h.t)+'</span>'+esc(h.quote||'')+'</div>'+
    '<div class=ex>好在：'+esc(h.why||'')+'</div></div>').join('');
  const au=rd.audience||{};
  $('main').innerHTML='<div class=grid><div class=pane><div class=ph><span>第 '+rd._meta.idx+
    ' 轮 · '+esc(rd._meta.product||'')+'</span><em>'+mmss(rd._meta.dur)+'</em></div><div class=pc>'+
    '<div class=bt>循环完整性</div><div style="font-size:12.5px;line-height:1.7">'+esc(st.cycle||'')+'</div>'+
    (st.missing&&st.missing.length?'<div class=alert><b>缺环节：'+esc(st.missing.join('、'))+'</b></div>':'')+
    (st.time_split?'<div class=bt style="margin-top:12px">时间配比</div><div style="font-size:12.5px">'+
      esc(st.time_split)+'</div>':'')+
    '<div class=bt style="margin-top:12px">价格塑造</div><div style="font-size:12.5px">'+esc(rd.price_pitch||'—')+'</div>'+
    '<div class=bt style="margin-top:12px">紧迫感手法</div><div style="font-size:12.5px">'+
    ((rd.urgency_moves||[]).map(u=>'• '+esc(u)).join('<br>')||'—')+'</div>'+
    '<div class=bt style="margin-top:12px">行动指令</div><div style="font-size:12.5px">共 '+
    (rd.cta?rd.cta.count:'—')+' 次 · '+esc(rd.cta?rd.cta.clarity:'')+
    ((rd.cta&&rd.cta.examples||[]).map(e=>'<div class=ex>'+esc(e)+'</div>').join(''))+'</div></div></div>'+
    '<div class=pane><div class=ph><span>逻辑链与问题</span></div><div class=pc>'+
    '<div class=bt>讲解逻辑链</div>'+(chains||'<div class=empty>无</div>')+
    '<div class=bt style="margin-top:13px">问题</div>'+(probs||'<div class=empty>无</div>')+'</div></div>'+
    '<div><div class=pane style=margin-bottom:12px><div class=ph>单轮评分</div><div class=pc>'+bars+
    '<div class=bt style="margin-top:10px">总评</div><div style="font-size:12.5px">'+esc(sc.comment||'')+
    '</div></div></div><div class=pane><div class=ph><span>亮点</span></div><div class=pc>'+
    (hl||'<div class=empty>无</div>')+'</div><div class=ph><span>观众</span></div><div class=pc>'+
    '<div style="font-size:12.5px">关注：'+esc((au.concerns||[]).join('、')||'—')+'</div>'+
    '<div style="font-size:12.5px;margin-top:4px">漏答：<span style="color:var(--bad)">'+
    esc((au.ignored||[]).join('、')||'—')+'</span></div></div></div></div>'+
    '<div style="margin-top:11px"><button class=tab onclick="render()">&larr; 返回轮次列表</button></div>';
}
boot();
</script></html>
"""


def main(out):
    p = pathlib.Path(out) / "session.html"
    head, sep, rest = TPL.partition("<script>")
    assert sep, "模板里没找到 <script>"
    js, sep2, tail = rest.rpartition("</script>")
    assert sep2, "模板里没找到 </script>"
    assert "<script>" not in js, "内联 <script> 不止一个，拆分会错位"
    p.write_text(head + '<script src="session.js"></script>' + tail, encoding="utf-8")
    (pathlib.Path(out) / "session.js").write_text(js, encoding="utf-8")
    print("  写入 %s" % p.name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out/site")
