const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
async function setup(markup,initialStorage={}){
 const dom=new JSDOM('<!doctype html><title>合成日语学习</title><div role="region" aria-label="对话" tabindex="0">'+markup+'</div>',{url:'https://chatgpt.com/c/synthetic-test',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;let panel;const attach=w.Element.prototype.attachShadow;
 w.Element.prototype.attachShadow=function(opts){const s=attach.call(this,opts);panel=s;return s;};
 let saved=structuredClone(initialStorage);w.chrome={storage:{local:{get:async()=>saved,set:async data=>{saved=structuredClone(data);}}},runtime:{sendMessage:async()=>({ok:false,error:'synthetic failure'})}};
 w.structuredClone=structuredClone;w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;w.Blob=Blob;w.Response=Response;w.CompressionStream=CompressionStream;w.DecompressionStream=DecompressionStream;Object.defineProperty(w.crypto,'subtle',{value:require('node:crypto').webcrypto.subtle});
 const timer=w.setTimeout.bind(w);w.setTimeout=(f,ms,...args)=>timer(f,ms>=500?2:ms,...args);
 for(const file of ['core.js','zip.js','volumes.js','store.js','collector.js'])w.eval(fs.readFileSync(path.join(__dirname,'..',file),'utf8'));
 const ready=Date.now()+5000;while(panel.querySelector('#start').disabled&&Date.now()<ready)await new Promise(r=>setTimeout(r,10));return {dom,w,panel,getSaved:()=>saved};
}
test('current layout: preserves Japanese tables, source IDs and download references',async()=>{
 const {dom,w}=await setup('<div><h4 data-conversation-role="user">你说：</h4><p>继续</p></div><div data-chatgpt-search-message-ids="answer-1 answer-1"><h4 data-conversation-role="assistant">ChatGPT 说：</h4><p>これは本です。</p><table><tr><td>ね</td><td>ne</td></tr></table><button aria-label="下载 japanese-starter.zip">资料</button><button>复制</button><script>bad()</script></div>');
 try{const m=w.MemoryBridgeController.extract(w.MemoryBridgeController.region());assert.equal(m.length,2);assert.equal(m[0].id,'user-before:answer-1');assert.match(m[1].text,/これは本です/);assert.match(m[1].text,/ね\tne/);assert.doesNotMatch(m[1].text,/bad\(|复制/);assert.equal(m[1].assets[0].kind,'button');}finally{dom.window.close();}
});
test('legacy layout: repeated short replies keep their individual message IDs',async()=>{
 const {dom,w}=await setup('<div data-message-id="u1" data-message-author-role="user">继续</div><div data-message-id="a1" data-message-author-role="assistant">はい</div><div data-message-id="u2" data-message-author-role="user">继续</div>');
 try{const m=w.MemoryBridgeController.extract(w.MemoryBridgeController.region());assert.equal(m.length,3);assert.deepEqual(Array.from(m,x=>x.id),['u1','a1','u2']);}finally{dom.window.close();}
});
test('nested message IDs connect snapshots as image placeholders turn into content',async()=>{
 const ids=['11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222'];
 const markup=(user,assistant)=>`<div><h4 data-conversation-role="user">你说</h4><div data-chatgpt-search-message-ids="${ids[0]}">${user}</div></div><div><h4 data-conversation-role="assistant">回复</h4><div data-chatgpt-search-message-ids="${ids[1]}">${assistant}</div></div>`;
 const {dom,w}=await setup(markup('生成图片','正在生成'));
 try{
  const el=w.MemoryBridgeController.region(),first=w.MemoryBridgeController.extract(el);
  el.innerHTML=markup('生成图片<img src="https://images.openai.com/user.png">','<img src="https://images.openai.com/reply.png">');
  const second=w.MemoryBridgeController.extract(el);
  assert.deepEqual(Array.from(second,x=>x.id),ids);
  const result=w.BridgeCore.assemble([{index:0,messages:first},{index:1,messages:second}]);
  assert.equal(result.gaps,0);
  assert.equal(result.messages.length,2);
  assert.ok(result.messages.every(message=>message.assets.length===1));
 }finally{dom.window.close();}
});
test('user skill mentions, content headings and source links are kept',async()=>{
 const {dom,w}=await setup('<div><h4 data-conversation-role="user">你说：</h4><button>$teach</button> 教我日语</div><div data-chatgpt-search-message-ids="a1"><h4 data-conversation-role="assistant">ChatGPT 说：</h4><h4>第一课</h4><a href="https://example.com/lesson">教材</a></div>');
 try{const m=w.MemoryBridgeController.extract(w.MemoryBridgeController.region());assert.match(m[0].text,/\$teach/);assert.match(m[1].text,/第一课/);assert.match(m[1].text,/https:\/\/example.com\/lesson/);}finally{dom.window.close();}
});
test('automatic scan crosses virtualized windows, persists checkpoint and reaches both boundaries',async()=>{
 const {dom,w,panel,getSaved}=await setup('<div data-message-id="initial" data-message-author-role="user">初始</div>');
 try{
  const region=w.MemoryBridgeController.region();let top=0;
  const all=Array.from({length:50},(_,i)=>({role:i%2?'assistant':'user',text:i%2?'回复 '+i:'继续',id:'message-'+i}));
  function render(){const start=Math.max(0,Math.floor(top/100)-3),slice=all.slice(start,start+12);region.innerHTML=slice.map(m=>'<div data-message-id="'+m.id+'" data-message-author-role="'+m.role+'">'+m.text+'</div>').join('');}
  Object.defineProperties(region,{scrollTop:{get:()=>top,set:v=>{top=Math.max(0,Math.min(4400,v));render();}},clientHeight:{get:()=>600},scrollHeight:{get:()=>5000}});
  region.scrollBy=opts=>{region.scrollTop=top+opts.top;};render();panel.querySelector('#start').click();
  const until=Date.now()+8000;while(w.MemoryBridgeController.getJob().status!=='captured'&&Date.now()<until)await new Promise(r=>setTimeout(r,10));
  const job=w.MemoryBridgeController.getJob();assert.equal(job.status,'captured',panel.querySelector('#status').textContent);assert.ok(job.bounds.top&&job.bounds.bottom);
  const result=w.BridgeCore.assemble(job.snapshots);assert.equal(result.gaps,0);assert.equal(result.messages.length,50);assert.equal(result.messages[0].id,'message-0');assert.equal(result.messages.at(-1).id,'message-49');
  while(panel.querySelector('#start').disabled&&Date.now()<until)await new Promise(r=>setTimeout(r,10));assert.ok((await w.BridgeStore.decode(Object.values(getSaved())[0])).snapshots.length>3);
 }finally{dom.window.close();}
});
test('partial draft with no verified start finds the real scroller and waits through transient empty content',async()=>{
 const msg=i=>'<div data-message-id="m'+i+'" data-message-author-role="user">日本語 '+i+'</div>';
 const old={version:2,url:'https://chatgpt.com/c/synthetic-test',bounds:{top:false,bottom:false},status:'paused',
  snapshots:[{index:0,top:400,messages:[{id:'m4',role:'user',text:'日本語 4'}]}]};
 const {dom,w,panel}=await setup(msg(4),{'memoryBridge:v1:/c/synthetic-test':old});
 try{
  const content=w.MemoryBridgeController.region(),scroller=w.document.createElement('main');
  scroller.style.overflowY='auto';content.before(scroller);scroller.append(content);
  let top=400,emptied=false;const visits=[];
  const render=()=>{const start=Math.min(4,Math.floor(top/100));content.innerHTML=Array.from({length:4},(_,i)=>msg(start+i)).join('');};
  Object.defineProperties(scroller,{scrollTop:{get:()=>top,set:v=>{
   visits.push(v);top=Math.max(0,Math.min(400,v));
   if(top===0&&!emptied){emptied=true;content.innerHTML='';setTimeout(render,18);}else render();
  }},scrollHeight:{get:()=>800},clientHeight:{get:()=>400}});
  scroller.scrollBy=o=>{scroller.scrollTop=top+o.top;};render();
  assert.equal(w.MemoryBridgeController.region(),scroller);
  const sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};
  panel.querySelector('#assets').checked=false;
  await w.MemoryBridgeController.automate('retry');
  assert.equal(visits[0],0,'seeks the head before attempting to pin the tail');
  assert.equal(sent.filter(x=>x.type!=='bridge:release').at(-1).capture_status,'captured',sent.filter(x=>x.type!=='bridge:release').at(-1).error||sent.filter(x=>x.type!=='bridge:release').at(-1).message);
  const result=w.BridgeCore.assemble(w.MemoryBridgeController.getJob().snapshots);
  assert.deepEqual(Array.from(result.messages,m=>m.id),Array.from({length:8},(_,i)=>'m'+i));
 }finally{dom.window.close();}
});
test('export is escaped and includes original snapshots and a checksum manifest',async()=>{
 const {dom,w,panel}=await setup('<div data-message-id="u1" data-message-author-role="user">&lt;script&gt;alert(1)&lt;/script&gt;</div><div data-message-id="a1" data-message-author-role="assistant">你好，日本語。</div>');
 try{
  const job=w.MemoryBridgeController.getJob();job.snapshots=[{index:0,messages:w.MemoryBridgeController.extract(w.MemoryBridgeController.region())}];job.bounds={top:true,bottom:true};let blob;
  w.URL.createObjectURL=b=>{blob=b;return 'blob:synthetic';};w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){};
  panel.querySelector('#assets').checked=false;await w.MemoryBridgeController.exportZip();assert.ok(blob);const data=Buffer.from(await blob.arrayBuffer());
  assert.ok(data.includes(Buffer.from('manifest.json')));assert.ok(data.includes(Buffer.from('&lt;script&gt;alert(1)&lt;/script&gt;')));assert.ok(data.includes(Buffer.from('原始片段.json')));
 }finally{dom.window.close();}
});
test('restores an existing checkpoint without deleting or reordering its messages',async()=>{
 const checkpoint={version:1,title:'之前的课',url:'https://chatgpt.com/c/synthetic-test',snapshots:[{index:0,messages:[{id:'u1',role:'user',text:'旧消息',html:'旧消息'}]}],bounds:{top:true,bottom:false},status:'paused'};
 const {dom,w,panel}=await setup('<div data-message-id="new" data-message-author-role="user">网页新消息</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{assert.equal(w.MemoryBridgeController.getJob().snapshots[0].messages[0].text,'旧消息');assert.match(panel.querySelector('#status').textContent,/本地断点/);assert.equal(panel.querySelector('#start').disabled,false);}finally{dom.window.close();}
});
test('legacy checkpoint beyond the old 120 MiB estimate migrates, exports, then continues without losing old messages',async()=>{
 const original=Array.from({length:100},(_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:'旧消息 '+i,html:'<p>'+('日本語'.repeat(1600))+'</p>'}));
 const snapshots=Array.from({length:180},(_,index)=>({index,top:4000,height:5000,viewport:600,messages:original}));
 assert.ok(JSON.stringify(original).length*2*snapshots.length>120*1024*1024);
 const legacy={version:1,title:'旧大断点',url:'https://chatgpt.com/c/synthetic-test',snapshots,bounds:{top:true,bottom:false},status:'paused'};
 const markup=Array.from({length:6},(_,n)=>{const i=n+98;return '<div data-message-id="m'+i+'" data-message-author-role="'+(i%2?'assistant':'user')+'">'+(i<100?'旧消息 '+i:'新增 '+i)+'</div>';}).join('');
 const {dom,w,panel,getSaved}=await setup(markup,{'memoryBridge:v1:/c/synthetic-test':legacy});
 try{
  assert.equal(Object.values(getSaved())[0].codec,'bridge-gzip-v2');assert.equal(w.MemoryBridgeController.getJob().snapshots.length,180);
  let requested=false;w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};w.HTMLAnchorElement.prototype.click=function(){requested=true;};panel.querySelector('#assets').checked=false;
  await w.MemoryBridgeController.exportZip();assert.equal(requested,true);assert.equal(w.MemoryBridgeController.getJob().snapshots.length,180);
  const el=w.MemoryBridgeController.region();let pos=0;const visited=[];Object.defineProperties(el,{scrollTop:{get:()=>pos,set:v=>{pos=Math.max(0,Math.min(4400,v));visited.push(pos);}},scrollHeight:{get:()=>5000},clientHeight:{get:()=>600}});el.scrollBy=o=>{el.scrollTop=pos+o.top;};
  panel.querySelector('#start').click();const until=Date.now()+10000;while((panel.querySelector('#start').disabled||w.MemoryBridgeController.getJob().status!=='captured')&&Date.now()<until)await new Promise(r=>setTimeout(r,10));
  assert.ok(visited.includes(3400),'returns one viewport before the saved position after checking the latest tail');assert.ok(!visited.includes(0),'does not reread from the head');const job=w.MemoryBridgeController.getJob();assert.equal(job.status,'captured',panel.querySelector('#status').textContent);const r=w.BridgeCore.assemble(job.snapshots);assert.equal(r.gaps,0);assert.equal(r.messages.length,104);assert.equal(r.messages[0].id,'m0');assert.equal(r.messages.at(-1).id,'m103');
  const restored=await w.BridgeStore.decode(Object.values(getSaved())[0]);assert.equal(w.BridgeCore.assemble(restored.snapshots).messages.length,104);
 }finally{dom.window.close();}
});


test('desktop automation scans then sends ZIP chunks and completion without a browser download',async()=>{
 const {dom,w}=await setup('<div data-message-id="u1" data-message-author-role="user">自动导出</div><div data-message-id="a1" data-message-author-role="assistant">日本語</div>');
 try{
  const el=w.MemoryBridgeController.region();Object.defineProperties(el,{scrollTop:{get:()=>0,set:()=>{}},scrollHeight:{get:()=>600},clientHeight:{get:()=>600}});el.scrollBy=()=>{};
  const calls=[];w.chrome.runtime.sendMessage=async request=>{calls.push(request);return {ok:true};};
  await w.MemoryBridgeController.automate('desktop-task');
  const uploads=calls.filter(c=>c.type==='bridge:upload');assert.ok(uploads.length);assert.ok(uploads.at(-1).final);
  const bytes=Buffer.concat(uploads.map(c=>Buffer.from(c.base64,'base64')));
  assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'),uploads.at(-1).sha256);
  assert.ok(bytes.includes(Buffer.from('manifest.json')));assert.ok(bytes.includes(Buffer.from('日本語')));
  assert.equal(calls.filter(x=>x.type!=='bridge:release').at(-1).type,'bridge:done');assert.ok(!calls.filter(x=>x.type!=='bridge:release').at(-1).error);assert.equal(calls.filter(x=>x.type!=='bridge:release').at(-1).files.length,1);
 }finally{dom.window.close();}
});


test('completed capture resumes at old boundary and sends delta while retaining both histories',async()=>{
 const old=Array.from({length:50},(_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:i===0?'ONLY_OLD_HEAD':'消息 '+i,html:'<p>'+i+'</p>'}));
 const checkpoint={version:2,title:'增量测试',url:'https://chatgpt.com/c/synthetic-test',snapshots:[{index:0,top:4400,messages:old}],bounds:{top:true,bottom:true},status:'captured',lastDelivered:{receiptVersion:1,snapshots:1}};
 const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  const el=w.MemoryBridgeController.region();let pos=4400;const visits=[];
  const all=[...old,...Array.from({length:20},(_,i)=>({id:'m'+(50+i),role:i%2?'assistant':'user',text:'新增 '+(50+i)}))];
  function render(){const start=Math.max(0,Math.floor(pos/100)-3);el.innerHTML=all.slice(start,start+12).map(m=>'<div data-message-id="'+m.id+'" data-message-author-role="'+m.role+'">'+m.text+'</div>').join('');}
  Object.defineProperties(el,{scrollTop:{get:()=>pos,set:v=>{pos=Math.max(0,Math.min(6400,v));visits.push(pos);render();}},scrollHeight:{get:()=>7000},clientHeight:{get:()=>600}});el.scrollBy=o=>{el.scrollTop=pos+o.top;};render();
  const sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};
  await w.MemoryBridgeController.exportZip({desktopTask:'old-task'});
  const firstBytes=Buffer.concat(sent.filter(x=>x.type==='bridge:upload').map(x=>Buffer.from(x.base64,'base64')));sent.length=0;
  await w.MemoryBridgeController.automate('delta-task');
  const job=w.MemoryBridgeController.getJob();assert.equal(job.status,'captured');assert.ok(job.continuityVerified);
  assert.ok(Math.min(...visits)>0,'does not restart at the conversation head');
  assert.equal(w.BridgeCore.assemble(job.snapshots).messages.length,70);
  const bytes=Buffer.concat(sent.filter(x=>x.type==='bridge:upload').map(x=>Buffer.from(x.base64,'base64')));
  assert.ok(bytes.length);assert.ok(!bytes.includes(Buffer.from('ONLY_OLD_HEAD')));assert.ok(bytes.includes(Buffer.from('新增 69')));
  assert.equal(job.lastDelivered.snapshots,job.snapshots.length);assert.ok(!sent.filter(x=>x.type!=='bridge:release').at(-1).error);
  if(process.env.MB_INCREMENTAL_DIR){fs.writeFileSync(path.join(process.env.MB_INCREMENTAL_DIR,'old.zip'),firstBytes);fs.writeFileSync(path.join(process.env.MB_INCREMENTAL_DIR,'delta.zip'),bytes);}
 }finally{dom.window.close();}
});


test('changed branch without old anchors pauses and retains both segments',async()=>{
 const checkpoint={version:2,title:'old',url:'https://chatgpt.com/c/synthetic-test',snapshots:[{index:0,top:1000,messages:[{id:'old-a',role:'user',text:'旧记录'},{id:'old-b',role:'assistant',text:'旧答案'}]}],bounds:{top:true,bottom:true},status:'captured',lastDelivered:{snapshots:1}};
 const {dom,w}=await setup('<div data-message-id="other-a" data-message-author-role="user">新分支</div><div data-message-id="other-b" data-message-author-role="assistant">新答案</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  const el=w.MemoryBridgeController.region();Object.defineProperties(el,{scrollTop:{get:()=>0,set:()=>{}},scrollHeight:{get:()=>600},clientHeight:{get:()=>600}});el.scrollBy=()=>{};
  const sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};await w.MemoryBridgeController.automate('branch');
  const job=w.MemoryBridgeController.getJob();assert.equal(job.status,'paused');assert.ok(!job.continuityVerified);
  const result=w.BridgeCore.assemble(job.snapshots);assert.equal(result.messages.length,4);assert.ok(result.gaps);
  assert.equal(sent.filter(x=>x.type!=='bridge:release').at(-1).capture_status,'partial');assert.ok(sent.some(x=>x.type==='bridge:upload'));
  const bytes=Buffer.concat(sent.filter(x=>x.type==='bridge:upload').map(x=>Buffer.from(x.base64,'base64')));
  assert.ok(bytes.includes(Buffer.from('旧记录')));assert.ok(bytes.includes(Buffer.from('新分支')));
 }finally{dom.window.close();}
});

function resumedFixture(delivery){
 const old=Array.from({length:50},(_,i)=>({id:'m'+i,role:i%2?'assistant':'user',text:i===0?'UNEXPORTED_OLD_HEAD':'消息 '+i,html:'<p>'+i+'</p>'}));
 return {old,checkpoint:{version:2,title:'未导出的日语断点',url:'https://chatgpt.com/c/synthetic-test',snapshots:[{index:0,top:4400,messages:old}],bounds:{top:true,bottom:false},status:'paused',...(delivery?{lastDelivered:delivery}:{})}};
}
function scrollingPage(w,old,count=90,lazy=false){
 const el=w.MemoryBridgeController.region();let pos=4400,loaded=lazy?50:count,pokes=0;
 const visits=[],all=[...old,...Array.from({length:count-50},(_,i)=>({id:'m'+(50+i),role:i%2?'assistant':'user',text:'新增 '+(50+i)}))];
 function render(){const start=Math.max(0,Math.floor(pos/100)-3);el.innerHTML=all.slice(start,Math.min(loaded,start+12)).map(m=>'<div data-message-id="'+m.id+'" data-message-author-role="'+m.role+'">'+m.text+'</div>').join('');}
 Object.defineProperties(el,{scrollTop:{get:()=>pos,set:v=>{if(lazy&&v>=loaded*100-600&&++pokes>=8)loaded=count;pos=Math.max(0,Math.min(loaded*100-600,v));visits.push(pos);render();}},scrollHeight:{get:()=>loaded*100},clientHeight:{get:()=>600}});
 el.scrollBy=o=>{el.scrollTop=pos+o.top;};render();return {visits,all};
}
const uploadBytes=sent=>Buffer.concat(sent.filter(x=>x.type==='bridge:upload').map(x=>Buffer.from(x.base64,'base64')));

test('paused checkpoint without any ZIP resumes beyond the pause and exports old and new content together',async()=>{
 for(const delivery of [undefined,{snapshots:1,at:'legacy-unacknowledged'}]){
  const {old,checkpoint}=resumedFixture(delivery);
  const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
  try{
   const {visits}=scrollingPage(w,old),sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};
   await w.MemoryBridgeController.automate('recover-no-zip');
   const job=w.MemoryBridgeController.getJob(),bytes=uploadBytes(sent);
   assert.equal(job.status,'captured');assert.equal(w.BridgeCore.assemble(job.snapshots).messages.length,90);
   assert.ok(bytes.includes(Buffer.from('UNEXPORTED_OLD_HEAD')));assert.ok(bytes.includes(Buffer.from('新增 89')));
   assert.ok(!visits.includes(0),'does not repeat the full capture');assert.equal(sent.filter(x=>x.type!=='bridge:release').at(-1).capture_status,'captured');
   assert.equal(job.lastDelivered.receiptVersion,1);assert.equal(job.lastDelivered.snapshots,job.snapshots.length);
   if(process.env.MB_RESUME_DIR&&!delivery)fs.writeFileSync(path.join(process.env.MB_RESUME_DIR,'resumed-full.zip'),bytes);
  }finally{dom.window.close();}
 }
});

test('temporary bottom loads more messages before completion instead of stopping at the old pause',async()=>{
 const {old,checkpoint}=resumedFixture();
 const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  scrollingPage(w,old,90,true);const sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};
  await w.MemoryBridgeController.automate('lazy-tail');
  assert.equal(w.MemoryBridgeController.getJob().status,'captured');
  assert.equal(w.BridgeCore.assemble(w.MemoryBridgeController.getJob().snapshots).messages.length,90);
  assert.ok(uploadBytes(sent).includes(Buffer.from('新增 89')));
 }finally{dom.window.close();}
});

test('failed ZIP transfer and failed completion acknowledgement never advance the delivery checkpoint',async()=>{
 for(const failure of ['bridge:upload','bridge:done']){
  const {old,checkpoint}=resumedFixture();
  const {dom,w,getSaved}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
  try{
   scrollingPage(w,old);let fail=true;const sent=[];
   w.chrome.runtime.sendMessage=async data=>{sent.push(data);return fail&&data.type===failure?{ok:false,error:'synthetic interrupted'}:{ok:true};};
   await w.MemoryBridgeController.automate('interrupted');
   assert.equal(w.MemoryBridgeController.getJob().lastDelivered,undefined);
   const restored=await w.BridgeStore.decode(Object.values(getSaved())[0]);assert.equal(restored.lastDelivered,undefined);
   assert.equal(w.BridgeCore.assemble(restored.snapshots).messages.length,90);
   fail=false;sent.length=0;await w.MemoryBridgeController.automate('retry');
   const bytes=uploadBytes(sent);assert.ok(bytes.includes(Buffer.from('UNEXPORTED_OLD_HEAD')));assert.ok(bytes.includes(Buffer.from('新增 89')));
   assert.equal(w.MemoryBridgeController.getJob().lastDelivered.receiptVersion,1);
  }finally{dom.window.close();}
 }
});

test('unacknowledged incremental delivery retries every new message after the last confirmed backup',async()=>{
 const {old,checkpoint}=resumedFixture({receiptVersion:1,snapshots:1});
 const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  scrollingPage(w,old);let fail=true;const sent=[];
  w.chrome.runtime.sendMessage=async data=>{sent.push(data);return fail&&data.type==='bridge:done'?{ok:false,error:'completion interrupted'}:{ok:true};};
  await w.MemoryBridgeController.automate('delta-failed');assert.equal(w.MemoryBridgeController.getJob().lastDelivered.snapshots,1);
  fail=false;sent.length=0;await w.MemoryBridgeController.automate('delta-retry');
  const bytes=uploadBytes(sent);assert.ok(bytes.includes(Buffer.from('新增 50')));assert.ok(bytes.includes(Buffer.from('新增 89')));
  assert.ok(!bytes.includes(Buffer.from('UNEXPORTED_OLD_HEAD')));
 }finally{dom.window.close();}
});

test('a stale resumed bottom cannot pass the latest-message check and is saved as partial',async()=>{
 const {old,checkpoint}=resumedFixture();
 const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  const el=w.MemoryBridgeController.region();let pos=4400,limit=9000,endVisits=0;
  const all=[...old,...Array.from({length:40},(_,i)=>({id:'m'+(50+i),role:i%2?'assistant':'user',text:'新增 '+(50+i)}))];
  function render(){const start=Math.max(0,Math.floor(pos/100)-3);el.innerHTML=all.slice(start,Math.min(limit/100,start+12)).map(m=>'<div data-message-id="'+m.id+'" data-message-author-role="'+m.role+'">'+m.text+'</div>').join('');}
  Object.defineProperties(el,{scrollTop:{get:()=>pos,set:v=>{if(v<5000&&endVisits>=13)limit=5000;pos=Math.max(0,Math.min(limit-600,v));if(pos>=8400)endVisits++;render();}},scrollHeight:{get:()=>limit},clientHeight:{get:()=>600}});el.scrollBy=o=>{el.scrollTop=pos+o.top;};render();
  const sent=[];w.chrome.runtime.sendMessage=async data=>{sent.push(data);return {ok:true};};await w.MemoryBridgeController.automate('stale-bottom');
  assert.equal(w.MemoryBridgeController.getJob().status,'paused');assert.equal(w.MemoryBridgeController.getJob().bounds.bottom,false);
  assert.equal(sent.filter(x=>x.type!=='bridge:release').at(-1).capture_status,'partial');assert.match(sent.filter(x=>x.type!=='bridge:release').at(-1).message,/尚未回到/);
  const bytes=uploadBytes(sent);assert.ok(bytes.includes(Buffer.from('UNEXPORTED_OLD_HEAD')));assert.ok(bytes.includes(Buffer.from('新增 89')));
 }finally{dom.window.close();}
});


test('desktop starts from the head when its delivered archive is missing',async()=>{
 const {old,checkpoint}=resumedFixture({receiptVersion:1,task:'a'.repeat(32),snapshots:1});
 const actual=old.map((m,i)=>i===0?{...m,text:'FRESH_HEAD'}:m);
 const {dom,w}=await setup('<div data-message-id="m40" data-message-author-role="user">消息 40</div>',{'memoryBridge:v1:/c/synthetic-test':checkpoint});
 try{
  const {visits}=scrollingPage(w,actual),sent=[];
  w.chrome.runtime.sendMessage=async data=>{sent.push(data);return data.type==='bridge:resume'?{ok:true,restart:true}:{ok:true};};
  await w.MemoryBridgeController.automate('b'.repeat(32),'日语学习',false,true);
  assert.ok(visits.includes(0));assert.equal(sent[0].receipt.task,'a'.repeat(32));
  const bytes=uploadBytes(sent);
  assert.ok(bytes.includes(Buffer.from('FRESH_HEAD')));assert.ok(!bytes.includes(Buffer.from('UNEXPORTED_OLD_HEAD')));
  assert.ok(bytes.includes(Buffer.from('"from_start": true')));assert.ok(bytes.includes(Buffer.from('新增 89')));
  assert.equal(sent.filter(x=>x.type!=='bridge:release').at(-1).capture_status,'captured');
 }finally{dom.window.close();}
});
