const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const source=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');

test('split ZIP volumes stay one logical export and reassemble exact bytes',async()=>{
 const scope={TextEncoder,Uint8Array,DataView,Blob,crypto:require('node:crypto').webcrypto};scope.globalThis=scope;
 vm.runInNewContext(source('zip.js')+source('volumes.js'),scope);
 const bytes=Uint8Array.from({length:26000},(_,i)=>i%251),blob=new Blob([bytes]);const parts=[];
 for await(const part of scope.BridgeVolumes.split(blob,'one.zip',9000))parts.push(part);
 assert.ok(parts.length>1);assert.ok(parts.every(p=>p.blob.size<=9000));
 const chunks=[],infos=[];
 for(const part of parts){
  const data=Buffer.from(await part.blob.arrayBuffer());let offset=0,files={};
  while(data.readUInt32LE(offset)===0x04034b50){const size=data.readUInt32LE(offset+18),n=data.readUInt16LE(offset+26),extra=data.readUInt16LE(offset+28),name=data.subarray(offset+30,offset+30+n).toString();const start=offset+30+n+extra;files[name]=data.subarray(start,start+size);offset=start+size;}
  const info=JSON.parse(files['bridge-volume.json']);infos.push(info);chunks.push(files['payload.bin']);
  assert.equal(await scope.BridgeVolumes.sha(files['payload.bin']),info.hashes[info.index-1]);
 }
 assert.deepEqual(Buffer.concat(chunks),Buffer.from(bytes));assert.equal(new Set(infos.map(i=>i.set_id)).size,1);
});

test('rendered list keeps IDs, deduplicates sidebar links and reports scope',async()=>{
 const dom=new JSDOM('<a href="/c/one">日语</a><a href="/c/two">数学</a><a href="/c/one">日语</a><a href="https://example.com/c/foreign">外部</a>',{url:'https://chatgpt.com/',runScripts:'outside-only'});
 try{const w=dom.window;w.setTimeout=(f)=>setTimeout(f,1);w.eval(source('browser_control.js'));
  const result=await w.BridgeBrowserControl.list();assert.equal(result.items.length,2);assert.equal(result.items[0].title,'日语');assert.equal(result.scope,'rendered_sidebar_links');
 }finally{dom.window.close();}
});

test('desktop dispatcher pairs, reads list, starts capture once and transfers bytes',async()=>{
 let listener,interval,resetAt;const removed=[],created=[],navigated=[],saved={'memoryBridge:v1:/c/old':{},desktopActive:{id:'old'},desktopConnection:{token:'old'}},calls=[],scripts=[];let command={id:'list1',kind:'list'};
 const chrome={runtime:{id:'a'.repeat(32),getManifest:()=>({version:'1.3.4'}),getURL:()=> 'chrome-extension://'+'a'.repeat(32)+'/',onMessage:{addListener:f=>listener=f},onStartup:{addListener:()=>{}}},
  alarms:{create:()=>{},onAlarm:{addListener:()=>{}}},storage:{local:{
   get:async keys=>Object.fromEntries([keys].flat().map(key=>[key,saved[key]])),
   set:async value=>Object.assign(saved,value),remove:async key=>delete saved[key],
   clear:async()=>{for(const key of Object.keys(saved))delete saved[key];}
  }},
  tabs:{remove:async id=>removed.push(id),query:async()=>[{id:4,url:'https://chatgpt.com/',active:true}],get:async()=>({status:'complete',url:'https://chatgpt.com/c/test'}),create:async options=>{created.push(options.url);return {id:5};},update:async(id,options)=>{navigated.push(options.url);return {id};}},
  scripting:{executeScript:async opts=>{scripts.push(opts);return [{result:{items:[{url:'https://chatgpt.com/c/test',title:'日语'}]}}];}}};
 const fetch=async(url,opts)=>{calls.push({url,opts});if(url.endsWith('/pair')||url.endsWith('/connect'))return new Response(JSON.stringify({token:'token'}));if(url.endsWith('/commands')){const next=command;command=null;return new Response(JSON.stringify({command:next,cache_reset_at:resetAt}));}return new Response('{"ok":true}');};
 const context={chrome,fetch,Response,URLSearchParams,AbortSignal,Uint8Array,atob,setTimeout,setInterval:f=>interval=f,Date};
 vm.runInNewContext(source('desktop.js'),context);
 const send=(data,sender={id:chrome.runtime.id})=>new Promise(resolve=>listener(data,sender,resolve));
 assert.equal((await send({type:'bridge:pair',code:'123456'})).ok,true);
 assert.equal(saved['memoryBridge:v1:/c/old'],undefined);
 assert.equal(saved.cacheVersion,'1.3.4');
 await new Promise(r=>setTimeout(r,20));assert.ok(calls.some(c=>c.url.endsWith('/result')&&JSON.parse(c.opts.body).items?.[0].title==='日语'));
 command={id:'capture1',kind:'capture',url:'https://chatgpt.com/c/test'};await interval();
 assert.equal(saved.desktopActive.tabId,5);assert.ok(scripts.some(s=>s.files?.includes('volumes.js')));
 const before=scripts.length;await interval();assert.equal(scripts.length,before);
 assert.equal((await send({type:'bridge:upload',id:'capture1',part:1,name:'one.zip',offset:0,base64:btoa('ZIP'),final:true,sha256:'hash'},{id:chrome.runtime.id,tab:{id:5}})).ok,true);
 assert.ok(calls.some(c=>c.url.includes('/upload?')&&Buffer.from(c.opts.body).toString()==='ZIP'));
 assert.equal((await send({type:'bridge:done',id:'capture1',files:['one.zip'],capture_status:'partial',message:'部分备份，可继续'},{id:chrome.runtime.id,tab:{id:5}})).ok,true);
 assert.ok(calls.some(c=>c.url.endsWith('/result')&&JSON.parse(c.opts.body).capture_status==='partial'&&JSON.parse(c.opts.body).message==='部分备份，可继续'));
 assert.ok(saved.desktopActive.finishing);
 assert.equal(removed.length,0);
 await send({type:'bridge:release',id:'capture1'},{id:chrome.runtime.id,tab:{id:5}});
 assert.equal(saved.desktopActive,undefined);
 assert.equal(removed.length,0);
 await new Promise(r=>setTimeout(r,20));
 command={id:'capture2',kind:'capture',url:'https://chatgpt.com/c/next'};await interval();
 await send({type:'bridge:done',id:'capture2',files:['two.zip'],capture_status:'captured'},{id:chrome.runtime.id,tab:{id:5}});
 assert.equal(removed.length,0);
 await send({type:'bridge:release',id:'capture2'},{id:chrome.runtime.id,tab:{id:5}});
 assert.deepEqual(removed,[]);
 assert.equal(created.length,1);
 assert.ok(navigated.includes("https://chatgpt.com/c/next"));
 assert.equal(saved.desktopActive,undefined);
 await new Promise(r=>setTimeout(r,20));
 saved['memoryBridge:v1:/c/new']={};
 resetAt='2026-11-11T10:00:00+00:00';await interval();
 assert.equal(saved['memoryBridge:v1:/c/new'],undefined);
 assert.equal(saved.desktopConnection.token,'token');
 command={id:'restore1',kind:'restore',title:'Japanese',files:[]};await interval();
 assert.ok(scripts.some(script=>script.files?.includes('restore.js')));
 assert.equal(saved.desktopActive.id,'restore1');
 assert.equal(created.length,1);
 assert.ok(navigated.includes('https://chatgpt.com/'));
 assert.equal((await send({type:'bridge:restore-file',id:'restore1',file:0,offset:0},{id:chrome.runtime.id,tab:{id:5}})).ok,true);
 assert.ok(calls.some(call=>call.url.includes('/restore-file?')));
});
