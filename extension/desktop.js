'use strict';
const BASE='http://127.0.0.1:18765';
let ticking=false;
async function clearCaptureCache(upgrade=false){
 const version=chrome.runtime.getManifest().version;
 const {cacheVersion,desktopConnection,lastDesktopCacheReset,desktopWorkerTab}=await chrome.storage.local.get(['cacheVersion','desktopConnection','lastDesktopCacheReset','desktopWorkerTab']);
 if(upgrade&&cacheVersion===version)return;
 await chrome.storage.local.clear();
 await chrome.storage.local.set({
  cacheVersion:version,captureCacheEpoch:Date.now(),
  ...(!upgrade&&desktopConnection?{desktopConnection}:{}),
  ...(!upgrade&&Number.isInteger(desktopWorkerTab)?{desktopWorkerTab}:{}),
  ...(lastDesktopCacheReset?{lastDesktopCacheReset}:{})
 });
}
const cacheReady=clearCaptureCache(true);
cacheReady.catch(()=>{});
async function autoConnect(){
 const response=await fetch(BASE+'/connect',{method:'POST',headers:{'Content-Type':'application/json','X-Bridge-Origin':chrome.runtime.getURL('').replace(/\/$/,'')},body:'{}',signal:AbortSignal.timeout(4000)});
 if(!response.ok)throw Error('暂未连接，请打开软件；首次识别失败时使用备用配对');
 const connection=await response.json();await chrome.storage.local.set({desktopConnection:connection});return connection;
}
async function desktopRequest(path,body,raw=false){
 let {desktopConnection}=await chrome.storage.local.get('desktopConnection');
 if(!desktopConnection?.token)desktopConnection=await autoConnect();
 const response=await fetch(BASE+path,{method:body===undefined?'GET':'POST',headers:{'X-Bridge-Origin':chrome.runtime.getURL('').replace(/\/$/,''),Authorization:'Bearer '+desktopConnection.token,...(raw?{'Content-Type':'application/octet-stream'}:{'Content-Type':'application/json'})},body:body===undefined?undefined:(raw?body:JSON.stringify(body)),signal:AbortSignal.timeout(15000)});
 if(response.status===403){await chrome.storage.local.remove('desktopConnection');throw Error('连接已过期，正在自动重连');}
 if(response.status===400&&path.startsWith('/task?'))throw Error('desktop_task_expired');
 if(!response.ok)throw Error('软件连接中断，请稍后重试');
 const result=await response.json();if(result.error)throw Error(result.error);return result;
}
async function report(id,status,extra={}){return desktopRequest('/result',{id,status,...extra});}
async function taskTab(url){
 const {desktopWorkerTab}=await chrome.storage.local.get('desktopWorkerTab');
 if(Number.isInteger(desktopWorkerTab)){
  try{
   const tab=await chrome.tabs.get(desktopWorkerTab);
   if(/^https:\/\/chatgpt\.com\//.test(tab.url||'')){
    return await chrome.tabs.update(desktopWorkerTab,{url,active:true});
   }
  }catch(error){/* A closed processing tab is replaced on the next task. */}
 }
 const tab=await chrome.tabs.create({url,active:true});
 await chrome.storage.local.set({desktopWorkerTab:tab.id});
 return tab;
}
async function readyTab(id){
 const deadline=Date.now()+60000;
 while(Date.now()<deadline){const tab=await chrome.tabs.get(id);if(tab.status==='complete'&&/^https:\/\/chatgpt.com\//.test(tab.url||''))return;await new Promise(r=>setTimeout(r,500));}
 throw Error('网页加载超时；请确认已登录 ChatGPT');
}
async function tick(){
 if(ticking)return;ticking=true;
 try{
  await cacheReady;
  const {desktopActive}=await chrome.storage.local.get('desktopActive');
  if(desktopActive){
   if(desktopActive.finishing&&Date.now()-desktopActive.finishing<30000)return;
   let state;
   try{state=await desktopRequest('/task?id='+desktopActive.id);}
   catch(e){if(e.message==='desktop_task_expired'){
     await chrome.scripting.executeScript({target:{tabId:desktopActive.tabId},func:()=>{
      globalThis.MemoryBridgeController?.stop();globalThis.MemoryBridgeRestore?.stop();
     }}).catch(()=>{});
     await chrome.storage.local.remove('desktopActive');return;
   }throw e;}
   if(['cancelled','completed','error'].includes(state.status)){
    await chrome.scripting.executeScript({target:{tabId:desktopActive.tabId},func:()=>{
     globalThis.MemoryBridgeController?.stop();globalThis.MemoryBridgeRestore?.stop();
    }}).catch(()=>{});
    if(state.status==='cancelled'&&desktopActive.kind==='restore'){
     await chrome.tabs.update(desktopActive.tabId,{url:desktopActive.lastChatURL||'https://chatgpt.com/'}).catch(()=>{});
    }
    await chrome.storage.local.remove('desktopActive');return;
   }
   try{await chrome.tabs.get(desktopActive.tabId);}catch(e){await report(desktopActive.id,'error',{error:'采集页面已关闭，断点保留。'});await chrome.storage.local.remove('desktopActive');}
   return;
  }
  const {command,cache_reset_at}=await desktopRequest('/commands');
  try{
   if(cache_reset_at){
    const {lastDesktopCacheReset}=await chrome.storage.local.get('lastDesktopCacheReset');
    if(!lastDesktopCacheReset||cache_reset_at>lastDesktopCacheReset){
     await clearCaptureCache();
     await chrome.storage.local.set({lastDesktopCacheReset:cache_reset_at});
    }
   }
   if(!command)return;
   await report(command.id,'running');
   if(command.kind==='list'){
    const tabs=await chrome.tabs.query({url:'https://chatgpt.com/*'});
    const tab=tabs.find(t=>t.active)||tabs[0];if(!tab)throw Error('请先在浏览器打开并登录 ChatGPT');
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['browser_control.js']});
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>BridgeBrowserControl.list()});
    await report(command.id,'completed',results[0].result);
   }else if(command.kind==='restore'){
    if(command.resume_url&&!/^https:\/\/chatgpt\.com\/c\/[^/?#]+$/.test(command.resume_url))throw Error('续传链接不正确');
    const tab=await taskTab(command.resume_url||'https://chatgpt.com/');
    await chrome.storage.local.set({desktopActive:{id:command.id,tabId:tab.id,kind:command.kind,lastChatURL:command.resume_url||''}});
    await readyTab(tab.id);
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['restore.js']});
    await chrome.scripting.executeScript({target:{tabId:tab.id},func:task=>{
     MemoryBridgeRestore.importConversation(task);
    },args:[command]});
   }else if(['capture','capture_from_start'].includes(command.kind)){
    if(!/^https:\/\/chatgpt\.com\/c\/[A-Za-z0-9-]+$/.test(command.url))throw Error('无效对话链接');
    const tab=await taskTab(command.url);
    await chrome.storage.local.set({desktopActive:{id:command.id,tabId:tab.id,kind:command.kind}});
    await readyTab(tab.id);
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['core.js','zip.js','volumes.js','store.js','collector.js']});
    await chrome.scripting.executeScript({target:{tabId:tab.id},func:(id,title,fromStart,verifyArchive)=>{
      MemoryBridgeController.automate(id,title,fromStart,verifyArchive);
    },args:[command.id,command.title||'',command.kind==='capture_from_start',Boolean(command.library)]});
   }else throw Error('未知软件任务');
  }catch(e){if(command)await report(command.id,'error',{error:e.message});await chrome.storage.local.remove('desktopActive');}
 }catch(e){/* Software may be closed; never log private message bodies. */}
 finally{ticking=false;}
}
chrome.runtime.onMessage.addListener((request,sender,respond)=>{
 if(!['bridge:ready','bridge:release','bridge:pair','bridge:resume','bridge:restore-check','bridge:restore-file','bridge:upload','bridge:done','bridge:progress'].includes(request?.type))return;
 (async()=>{
  if(sender.id!==chrome.runtime.id)throw Error('无效插件来源');
  await cacheReady;
  if(request.type==='bridge:ready')return {ok:true};
  if(request.type==='bridge:pair'){
   const response=await fetch(BASE+'/pair',{method:'POST',headers:{'Content-Type':'application/json','X-Bridge-Origin':chrome.runtime.getURL('').replace(/\/$/,'')},body:JSON.stringify({code:request.code}),signal:AbortSignal.timeout(8000)});
   if(!response.ok)throw Error('配对失败，请核对软件中的连接码');
   const value=await response.json();await chrome.storage.local.set({desktopConnection:value});
   await chrome.storage.local.remove('desktopActive');tick();return {ok:true};
  }
  const {desktopActive}=await chrome.storage.local.get('desktopActive');
  if(!desktopActive||desktopActive.id!==request.id||desktopActive.tabId!==sender.tab?.id)throw Error('任务来源不匹配');
  if(request.type==='bridge:restore-check'){
   const state=await desktopRequest('/task?id='+request.id);
   if(state.status!=='running')throw Error('上传已中断');
   return {ok:true};
  }
  if(request.type==='bridge:release'){
   if(!desktopActive.finishing)throw Error('任务尚未确认送达');
   await chrome.storage.local.remove('desktopActive');tick();return {ok:true};
  }
  if(request.type==='bridge:restore-file'){
   const query=new URLSearchParams({id:request.id,file:request.file,offset:request.offset});
   return {ok:true,...await desktopRequest('/restore-file?'+query)};
  }
  if(request.type==='bridge:resume'){
   const result=await desktopRequest('/resume',{id:request.id,receipt:request.receipt,draft_started:request.draft_started});
   return {ok:true,restart:result.restart};
  }
  if(request.type==='bridge:upload'){
   const bytes=Uint8Array.from(atob(request.base64),x=>x.charCodeAt(0));
   const query=new URLSearchParams({task:request.id,part:request.part,offset:request.offset,name:request.name,final:request.final?'1':'0',sha256:request.sha256||''});
   await desktopRequest('/upload?'+query,bytes,true);return {ok:true};
  }
  if(request.type==='bridge:progress'){
   if(request.chat_url&&/^https:\/\/chatgpt\.com\/c\//.test(request.chat_url)){
    await chrome.storage.local.set({desktopActive:{...desktopActive,lastChatURL:request.chat_url}});
   }
   await report(request.id,'running',{message:request.message,sent_files:request.sent_files||0,chat_url:request.chat_url||'',submission_uncertain:request.submission_uncertain===true,pending_marker:request.pending_marker||'',phase:request.phase||'files',final_sent:request.final_sent===true});return {ok:true};
  }
  await chrome.storage.local.set({desktopActive:{...desktopActive,finishing:Date.now()}});
  try{
   await report(request.id,request.error?'error':'completed',{error:request.error||'',files:request.files||[],message:request.message||'',capture_status:request.capture_status||'',chat_url:request.chat_url||'',sent_files:request.sent_files||0,submission_uncertain:request.submission_uncertain===true,rename_status:request.rename_status||'',rename_error:request.rename_error||'',pending_marker:request.pending_marker||'',phase:request.phase||'',final_sent:request.final_sent===true});
  }catch(error){
   await chrome.storage.local.set({desktopActive});throw error;
  }
  if(['capture','capture_from_start'].includes(desktopActive.kind)&&!request.error){
   await chrome.storage.local.set({desktopActive:{...desktopActive,finishing:Date.now()}});
   return {ok:true};
  }
  await chrome.storage.local.remove('desktopActive');tick();return {ok:true};
 })().then(respond,e=>respond({ok:false,error:e.message}));return true;
});
chrome.alarms.create('memory-bridge-desktop',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(tick);chrome.runtime.onStartup.addListener(tick);
setInterval(tick,3000);tick();
