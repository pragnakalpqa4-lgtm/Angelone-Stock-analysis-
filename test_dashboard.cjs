// UI smoke test against the actual embedded script, using isolated fixture data.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(__dirname+'/dashboard.html','utf8').split('<script>')[1].split('</script>')[0];
async function check(market,label,expectedPrice){
  const elements={};
  const ctx={clearRect(){},fillText(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillRect(){}};
  const document={getElementById(id){return elements[id]??={style:{},getContext:()=>ctx,width:900,height:280};}};
  const data={market,orders_disabled:true,server_ts:Date.now()/1000,expert_team:{reports:[{token:'99926000',readiness:'BLOCKED',explanation:'Fixture missing evidence',specialists:[{name:'<img src=x>',status:'BLOCKED',stance:'UNAVAILABLE',blockers:['No data'],evidence:{value:'<script>'}}]},{token:'99919000',readiness:'WARMING_UP',explanation:'Fixture waiting for closed candles',specialists:[]}]}};
  const sandbox={document,fetch:async()=>({ok:true,json:async()=>data}),setInterval(){},Date,Number,Object,Math};
  vm.createContext(sandbox);vm.runInContext(source,sandbox);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(elements.feed.textContent,label);assert.equal(elements.gate.textContent,'ALL ORDERS DISABLED');
  assert.equal(elements.nifty.textContent,expectedPrice);assert.equal(elements.decision.textContent,'NO TRADE');
  assert.ok(elements.teamCards.innerHTML.includes('&lt;img'));assert.ok(!elements.teamCards.innerHTML.includes('<img'));
  sandbox.showIndex('99919000');assert.equal(elements.teamTitle.textContent,'SENSEX research specialists');assert.equal(elements.teamReadiness.textContent,'WARMING UP');
  sandbox.fetch=async()=>({ok:false});await sandbox.refresh();assert.equal(elements.feed.textContent,'DASHBOARD DISCONNECTED');assert.equal(elements.teamCards.innerHTML,'');assert.equal(elements.decision.textContent,'NO TRADE');
}
(async()=>{
  assert.ok(!fs.readFileSync(__dirname+'/dashboard.html','utf8').includes('MCX'));
  await check({ticks:[],candles:{},instruments:[],fresh:false},'OFFLINE','—');
  const instruments=[{token:'99926000',symbol:'Nifty 50',downloaded_at_utc:'fixture'},{token:'99919000',symbol:'SENSEX',downloaded_at_utc:'fixture'}];
  const ticks=instruments.map(v=>({token:v.token,price:23000,fresh:true,exchange_age_seconds:1,receive_age_seconds:1}));
  await check({ticks,candles:{'99926000':[{open:23000,high:23001,low:22999,close:23001,minute:1}]},instruments,fresh:true},'BOTH FEEDS FRESH','23,000');
  ticks[1].fresh=false;ticks[1].exchange_age_seconds=100;
  await check({ticks,candles:{},instruments,fresh:false},'PARTIAL / STALE','23,000');
  console.log('Dashboard checks passed: index scope, offline/fresh/partial feeds, selection, specialist evidence escaping, disconnect veto.');
})().catch(error=>{console.error(error);process.exitCode=1});
