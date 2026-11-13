const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const {File}=require('node:buffer');
const crypto=require('node:crypto');
async function runImport(fail=false,workAvailable=true,textarea=false,localized=false,lazy=false,cancel=false,resume=0,finalReply=true,virtualized=false){
 const mode=workAvailable?'<div role="tablist"><button role="tab">聊天</button><button id="mode" '+(localized?'aria-selected':'aria-pressed')+'="false">'+(localized?'工作':'Work')+'</button></div>':'';
 const box=textarea?'<textarea placeholder="输入任务"></textarea>':'<div id="prompt-textarea" contenteditable="true"></div>';
 const dom=new JSDOM(mode+'<form>'+box+'<input type="file"><button type="button" data-testid="send-button">发送</button></form>',{
  url:'https://chatgpt.com/',runScripts:'outside-only'});
 const w=dom.window,calls=[],sent=[],input=w.document.querySelector('input');
 const nativeTimer=w.setTimeout.bind(w);w.setTimeout=(fn,ms)=>nativeTimer(fn,Math.min(ms,5));
 if(lazy){
  input.remove();
  const add=w.document.createElement('button');add.type='button';add.setAttribute('data-testid','composer-plus-btn');
  w.document.querySelector('form').append(add);
  add.onclick=()=>setTimeout(()=>w.document.querySelector('form').append(input),650);
 }
 let modeClicks=0;
 if(workAvailable){
  w.document.querySelector('#mode').onclick=()=>{
   modeClicks++;w.document.querySelector('#mode').setAttribute(localized?'aria-selected':'aria-pressed','true');
  };
 }else{
  let clock=0;w.Date.now=()=>{const value=clock;clock+=61000;return value;};
 }
 w.File=File;
 w.DataTransfer=class{
  constructor(){this.files=[];this.items={add:file=>this.files.push(file)};}
 };
 Object.defineProperty(w.crypto,'subtle',{value:crypto.webcrypto.subtle});
 let files=[];
 Object.defineProperty(input,'files',{get:()=>files,set:value=>{files=value;}});
 input.addEventListener('change',()=>{
  for(const file of files){const name=w.document.createElement('span');name.textContent=file.name;input.after(name);}
 });
 w.document.querySelector('[data-testid="send-button"]').onclick=()=>{
  assert.equal(w.document.querySelector('#mode').getAttribute(localized?'aria-selected':'aria-pressed'),'true');
  const editor=w.document.querySelector('#prompt-textarea,textarea');
  sent.push({text:textarea?editor.value:editor.textContent,files:Array.from(files,file=>file.name)});
  editor.textContent='';if(textarea)editor.value='';
  if(virtualized)w.document.querySelectorAll('[data-message-author-role="user"]').forEach(node=>node.remove());
  const message=w.document.createElement('div');message.setAttribute('data-message-author-role','user');message.textContent=sent.at(-1).text;w.document.body.append(message);
  w.history.replaceState(null,'','/c/new-chat');files=[];
  if(!finalReply&&sent.at(-1).text.startsWith('独立接续编号'))return;
  const reply=w.document.createElement('div');reply.setAttribute('data-message-author-role','assistant');reply.textContent='已收到 '+sent.length;w.document.body.append(reply);
 };
 const bytes=Buffer.from('synthetic Japanese 日本語'),sha=crypto.createHash('sha256').update(bytes).digest('hex');
 const task={id:'test',title:'Japanese',prompt:'独立接续编号',files:Array.from({length:7},(_,i)=>({path:'part'+i+'.md',size:bytes.length,sha256:sha}))};
 w.chrome={runtime:{sendMessage:async request=>{
  calls.push(request);
  if(cancel&&request.type==='bridge:restore-check')return {ok:false,error:'上传已中断'};
  if(request.type==='bridge:restore-file')return fail?{ok:false,error:'checksum error'}:{
   ok:true,base64:bytes.toString('base64'),name:'part'+request.file+'.md',size:bytes.length,sha256:sha};
  return {ok:true};
 }}};
 if(resume){
  task.resume_url='https://chatgpt.com/c/new-chat';task.resume_sent_files=resume;
  w.history.replaceState(null,'','/c/new-chat');w.document.querySelector('#mode').setAttribute('aria-pressed','true');
 }
 w.eval(fs.readFileSync(path.join(__dirname,'../restore.js'),'utf8'));
 try{await w.MemoryBridgeRestore.importConversation(task);return {calls,sent,modeClicks};}
 finally{w.close();}
}
test('restores batches in one new chat using only its independent prompt',async()=>{
 const {calls,sent,modeClicks}=await runImport();
 assert.equal(modeClicks,1);
 assert.equal(sent.length,3);
 assert.deepEqual(sent.map(x=>x.files.length),[5,2,0]);
 assert.ok(sent[2].text.includes('独立接续编号'));
 assert.match(sent[0].text,/等待单独/);
 assert.match(sent[2].text,/本条资料已上传结束/);
 assert.equal(calls.at(-1).chat_url,'https://chatgpt.com/c/new-chat');
 assert.ok(!calls.at(-1).error);
});
test('does not submit a message when loading its upload files fails',async()=>{
 const {calls,sent}=await runImport(true);
 assert.equal(sent.length,0);
 assert.equal(calls.at(-1).type,'bridge:done');
 assert.match(calls.at(-1).error,/checksum/);
});
test('stops before file transfer when Work mode cannot be confirmed',async()=>{
 const {calls,sent}=await runImport(false,false);
 assert.equal(sent.length,0);
 assert.ok(!calls.some(request=>request.type==='bridge:restore-file'));
 assert.match(calls.at(-1).error,/确认.*Work/);
});
test('supports a visible Work textarea without the legacy prompt element ID',async()=>{
 const {calls,sent}=await runImport(false,true,true);
 assert.equal(sent.length,3);
 assert.ok(sent[2].text.includes('独立接续编号'));
 assert.ok(!calls.at(-1).error);
});
test('recognizes the Chinese Chat and Work switch shown on the user page',async()=>{
 const {calls,sent,modeClicks}=await runImport(false,true,true,true);
 assert.equal(modeClicks,1);
 assert.equal(sent.length,3);
 assert.ok(!calls.at(-1).error);
});
test('waits for a new Work page to create its upload input after opening the menu',async()=>{
 const {calls,sent}=await runImport(false,true,true,true,true);
 assert.equal(sent.length,3);
 assert.equal(calls.at(-1).sent_files,7);
 assert.ok(!calls.at(-1).error);
});

test('interrupt prevents submission even after file bytes were loaded',async()=>{
 const {calls,sent}=await runImport(false,true,false,false,false,true);
 assert.equal(sent.length,0);
 assert.match(calls.at(-1).error,/上传已中断/);
});

test('continues the same chat while skipping acknowledged files',async()=>{
 const {calls,sent}=await runImport(false,true,false,false,false,false,5);
 assert.deepEqual(sent.map(turn=>turn.files.length),[2,0]);
 assert.ok(!calls.some(request=>request.type==='bridge:restore-file'&&request.file<5));
 assert.equal(calls.at(-1).sent_files,7);
 assert.equal(calls.at(-1).final_sent,true);
});
test('a missing model answer after the final acknowledgement does not discard uploaded files',async()=>{
 const {calls}=await runImport(false,true,false,false,false,false,0,false);
 assert.ok(!calls.at(-1).error);
 assert.equal(calls.at(-1).sent_files,7);
 assert.equal(calls.at(-1).final_sent,true);
});

test('acknowledges exact submitted messages when the page removes earlier user bubbles',async()=>{
 const {calls,sent}=await runImport(false,true,false,false,false,false,0,true,true);
 assert.equal(sent.length,3);
 assert.equal(calls.at(-1).sent_files,7);
 assert.equal(calls.at(-1).final_sent,true);
 assert.ok(!calls.at(-1).error);
});


test('Work attachments outside the editor wrappers are recognized by full card titles',()=>{
 const dom=new JSDOM('<main><section id="composer"><div title="学习记录(1).md">学习记…</div><div><div><div id="prompt-textarea" contenteditable="true"></div></div></div></section><div title="wrong.png"></div></main>',{url:'https://chatgpt.com/',runScripts:'outside-only'});
 try{
  dom.window.eval(fs.readFileSync(path.join(__dirname,'../restore.js'),'utf8'));
  const check=dom.window.MemoryBridgeRestore.composerAttachments;
  assert.equal(check(['学习记录.md']).id,'composer');
  assert.equal(check(['wrong.png']),null);
  assert.equal(check(['not-uploaded.png']),null);
 }finally{dom.window.close();}
});


test('last Work batch recognizes timestamp-renamed index JSON without using previous messages',()=>{
 const dom=new JSDOM('<main><section id="composer"><div title="附件索引(20261112-171845)(1).json">附件索引…</div><div title="last.png"></div><div id="prompt-textarea" contenteditable="true"></div></section></main>',{url:'https://chatgpt.com/',runScripts:'outside-only'});
 try{
  dom.window.eval(fs.readFileSync(path.join(__dirname,'../restore.js'),'utf8'));
  assert.equal(dom.window.MemoryBridgeRestore.composerAttachments(['附件索引.json','last.png']).id,'composer');
  assert.equal(dom.window.MemoryBridgeRestore.composerAttachments(['对话目录.json']),null);
 }finally{dom.window.close();}
});
