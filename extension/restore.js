(function(root){
 'use strict';
 const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 const visible=element=>element&&!element.closest('[hidden],[aria-hidden="true"]')
  &&getComputedStyle(element).display!=='none'&&getComputedStyle(element).visibility!=='hidden';
 const editor=()=>Array.from(document.querySelectorAll(
  'textarea#prompt-textarea,#prompt-textarea[contenteditable="true"],#prompt-textarea [contenteditable="true"],'
  +'[contenteditable="true"][data-lexical-editor="true"],textarea,[role="textbox"][contenteditable="true"],form [contenteditable="true"]'
 )).find(element=>visible(element)&&!element.disabled);
 const sendButton=()=>document.querySelector('[data-testid="send-button"],button[aria-label="发送提示"],button[aria-label="发送"],button[aria-label="Send prompt"]');
 const stopped=()=>document.querySelector('[data-testid="stop-button"],button[aria-label="Stop streaming"],button[aria-label="停止流式传输"],button[aria-label="停止生成"]');
 let cancelled=false;
 const workLabel=element=>{
  const label=(element.getAttribute('aria-label')||element.textContent||'').replace(/\s+/g,' ').trim();
  return /^(?:(?:ChatGPT|选择|切换到|切换至|Switch to|Select)\s*)?(?:Work|工作)$/i.test(label);
 };
 function workControls(){
  return Array.from(document.querySelectorAll('button,[role="tab"],[role="radio"],[role="menuitemradio"]'))
   .filter(element=>visible(element)&&!element.closest('aside')&&workLabel(element));
 }
 function workSelected(){
  if(workControls().some(element=>element.getAttribute('aria-selected')==='true'
   ||element.getAttribute('aria-pressed')==='true'||element.getAttribute('aria-checked')==='true'
   ||element.getAttribute('aria-current')==='page'||/^(active|checked|on|selected)$/.test(element.getAttribute('data-state')||'')))return true;
  const box=editor();
  return box&&/\bWork\b|ChatGPT\s*工作|工作模式/i.test([box.getAttribute('placeholder'),box.getAttribute('aria-label'),
   box.getAttribute('data-placeholder'),...Array.from(box.querySelectorAll('[data-placeholder]'),node=>node.getAttribute('data-placeholder'))].filter(Boolean).join(' '));
 }
 async function enterWork(task){
  await message(task,'bridge:progress',{message:'正在切换到网页 Work，请保持页面打开…'});
  const deadline=Date.now()+60000;
  let clicked=false;
  while(Date.now()<deadline){
   assertActive();
   if(workSelected())return;
   if(!clicked){
    const button=workControls().find(element=>!element.disabled&&element.getAttribute('aria-disabled')!=='true');
    if(button){button.click();clicked=true;}
   }
   await sleep(500);
  }
  throw Error('未能确认已进入网页 Work。请在新页面顶部手动选择 Work，核对账号权限后重试；本条尚未上传资料');
 }
 function assertActive(){
  if(cancelled)throw Error('导入已停止，已创建的聊天保留，请核对后继续');
  if(location.hostname!=='chatgpt.com')throw Error('请在 ChatGPT 页面导入');
 }
 async function until(check,message,timeout=180000){
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){
   assertActive();
   if(check())return;
   await sleep(500);
  }
  throw Error(message);
 }
 async function message(task,type,extra={}){
  assertActive();
  const result=await chrome.runtime.sendMessage({type,id:task.id,...extra});
  assertActive();
  if(!result?.ok)throw Error(result?.error||'软件连接中断');
  return result;
 }
 async function loadFile(task,index,item){
  const buffers=[];
  let name,offset=0;
  do{
   const chunk=await message(task,'bridge:restore-file',{file:index,offset});
   const bytes=Uint8Array.from(atob(chunk.base64),char=>char.charCodeAt(0));
   if(!bytes.length&&offset<item.size)throw Error('文件传输中断');
   buffers.push(bytes);offset+=bytes.length;name=chunk.name;
  }while(offset<item.size);
  const extension=name.split('.').pop().toLowerCase();
  const mime={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',gif:'image/gif',
   pdf:'application/pdf',md:'text/markdown',txt:'text/plain',json:'application/json'}[extension]||'application/octet-stream';
  const file=new File(buffers,name,{type:mime});
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),n=>n.toString(16).padStart(2,'0')).join('');
  if(file.size!==item.size||digest!==item.sha256)throw Error('上传文件校验失败');
  return file;
 }
 function putText(text){
  const box=editor();
  if(!box)throw Error('未找到聊天输入框，请确认已登录新账号');
  box.focus();
  if(box.tagName==='TEXTAREA'){
   Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(box,text);
  }else{
   const selection=window.getSelection(),range=document.createRange();
   range.selectNodeContents(box);selection.removeAllRanges();selection.addRange(range);
   const inserted=typeof document.execCommand==='function'&&document.execCommand('insertText',false,text);
   if(!inserted)box.replaceChildren(...text.split('\n').map(line=>{
    const paragraph=document.createElement('p');paragraph.textContent=line;return paragraph;
   }));
  }
  box.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));
 }
 // Work renders attachment cards outside the editor's immediate wrappers.
 // Read filenames from card attributes too: their visible labels may be truncated.
 function composerAttachments(names){
  const normalize=value=>String(value||'').normalize('NFC').replace(/\((?:\d{8}[-_]\d{6}(?:[-_]\w+)?|\d+)\)(?=\.[a-z0-9]+|\()/gi,'');
  let scope=editor();
  while(scope&&scope!==document.body&&scope.tagName!=='MAIN'){
   const labels=[scope.textContent||'',...Array.from(scope.querySelectorAll('[title],[aria-label],img[alt]'),
    node=>[node.getAttribute('title'),node.getAttribute('aria-label'),node.getAttribute('alt')].filter(Boolean).join(' '))].join('\n');
   if(names.every(name=>normalize(labels).includes(normalize(name))))return scope;
   scope=scope.parentElement;
  }
  return null;
 }
 const assistantStamp=()=>Array.from(document.querySelectorAll(
  '[data-message-author-role="assistant"],h4[data-conversation-role="assistant"]'
 )).map(node=>node.tagName==='H4'?node.parentElement.textContent:node.textContent).join('\n');
 const userContains=marker=>Array.from(document.querySelectorAll(
  '[data-message-author-role="user"],h4[data-conversation-role="user"]'
 )).some(node=>(node.tagName==='H4'?node.parentElement.textContent:node.textContent).includes(marker));
 async function sendTurn(task,text,onSent=async()=>{},waitForAnswer=true){
  const marker='MB_ACK_'+Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16)).join('_');
  task.pending_marker=marker;
  putText(text+'\n\n迁移校验码：'+marker);
  await until(()=>sendButton()&&!sendButton().disabled&&!stopped(),'网页未允许发送，请检查附件状态或账号额度');
  await message(task,'bridge:restore-check');
  const previous=assistantStamp();
  sendButton().click();
  try{
   await until(()=>userContains(marker)&&/\/c\//.test(location.pathname),
     '未能确认发送结果，已暂停。请检查此聊天，避免重复提交');
  }catch(error){error.submissionUncertain=true;throw error;}
  task.pending_marker='';await onSent();
  if(!waitForAnswer)return;
  await until(()=>stopped()||assistantStamp()!==previous,'未观察到本批回复开始，已暂停，请检查当前聊天');
  let stable=0,last='';
  await until(()=>{
   const stamp=assistantStamp();
   const box=editor(),empty=box&&!(box.tagName==='TEXTAREA'?box.value:box.textContent).trim();
   stable=!stopped()&&empty&&stamp!==previous&&stamp===last?stable+1:0;last=stamp;
   return stable>=4;
  },'等待本批回复结束超时，已暂停，请检查当前聊天');
 }
 async function uploadInput(){
  const find=()=>Array.from(document.querySelectorAll('input[type="file"]'))
   .find(node=>!node.disabled&&(!node.accept||node.accept.includes('*')||node.accept.includes('.')));
  let input=find();
  if(input)return input;
  const suppressPicker=event=>{
   if(event.target.matches?.('input[type="file"]'))event.preventDefault();
  };
  document.addEventListener('click',suppressPicker,true);
  try{
   let opened=false,chosen=false;
   const deadline=Date.now()+30000;
   while(Date.now()<deadline){
    assertActive();
    input=find();if(input)return input;
    const container=editor()?.closest('form')||editor()?.parentElement?.parentElement;
    if(!opened){
     const add=document.querySelector('[data-testid="composer-plus-btn"]')||
      Array.from(container?.querySelectorAll('button')||[]).find(button=>
       /添加.*(?:文件|照片|工具)|附件|attach|upload|add (?:files|photos)/i.test(button.getAttribute('aria-label')||button.textContent));
     if(add&&!add.disabled){add.click();opened=true;}
    }else if(!chosen){
     const option=Array.from(document.querySelectorAll('[role="menuitem"],[role="option"],button'))
      .find(node=>visible(node)&&/^(?:添加照片和文件|添加照片与文件|上传文件|添加文件|Add photos (?:&|and) files|Upload files|Add files)$/i
       .test((node.getAttribute('aria-label')||node.textContent||'').trim()));
     if(option){option.click();chosen=true;}
    }
    await sleep(250);
   }
   throw Error('未找到可用的上传控件，请检查网页的“＋”上传菜单或账号额度；后续对话已暂停');
  }finally{document.removeEventListener('click',suppressPicker,true);}
 }
 async function renameConversation(title,checkTask=async()=>{}){
  const normalize=value=>String(value||'').replace(/\s+/g,' ').trim();
  const sameChat=link=>{
   try{return new URL(link.href,location.href).pathname===location.pathname;}
   catch(error){return false;}
  };
  const links=()=>Array.from(document.querySelectorAll('a[href]')).filter(sameChat);
  const renamed=()=>links().some(link=>normalize(link.textContent)===normalize(title)
   ||normalize(link.getAttribute('title'))===normalize(title));
  if(renamed())return;
  const link=links()[0];
  if(!link)throw Error('当前聊天未出现在侧边栏，名称未修改');
  const before=new Set(document.querySelectorAll('input'));
  let row=link,menu;
  for(let level=0;row&&level<5;level++,row=row.parentElement){
   if(Array.from(row.querySelectorAll('a[href]')).filter(node=>/\/c\//.test(node.getAttribute('href')||'')).length>1)break;
   row.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
   menu=Array.from(row.querySelectorAll('button')).find(button=>
    /更多|选项|菜单|more|options|menu/i.test(button.getAttribute('aria-label')||button.getAttribute('data-testid')||''));
   if(menu)break;
  }
  if(!menu)throw Error('未找到当前聊天的重命名菜单');
  await checkTask();
  menu.click();
  let option;
  await until(()=>{
   option=Array.from(document.querySelectorAll('[role="menuitem"],button')).find(node=>
    visible(node)&&/^(重命名|重新命名|Rename)$/i.test(normalize(node.textContent||node.getAttribute('aria-label'))));
   return option;
  },'未找到重命名选项',10000);
  await checkTask();option.click();
  let input;
  await until(()=>{
   input=Array.from(document.querySelectorAll('input')).find(node=>visible(node)&&!node.disabled
    &&(node.type==='text'||!node.getAttribute('type'))&&
    (node.closest('[role="dialog"]')||!before.has(node)||row?.contains(node)));
   return input;
  },'未找到名称输入框',10000);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,title);
  input.dispatchEvent(new Event('input',{bubbles:true}));
  input.dispatchEvent(new Event('change',{bubbles:true}));
  await checkTask();
  const scope=input.closest('[role="dialog"]')||row;
  const save=Array.from(scope?.querySelectorAll('button')||[]).find(node=>
   visible(node)&&!node.disabled&&/^(保存|确认|完成|Save|Confirm|Done)$/i.test(normalize(node.textContent)));
  if(save)save.click();
  else{
   input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));
   input.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',bubbles:true}));
  }
  await until(renamed,'名称保存未获确认，请在侧边栏核对',10000);
 }
 async function importConversation(task){
  cancelled=false;
  let sent=task.resume_sent_files||0,finalSent=Boolean(task.resume_final_sent),phase='files';
  const checkpoint=()=>({sent_files:sent,final_sent:finalSent,phase,submission_uncertain:false,pending_marker:task.pending_marker||'',chat_url:location.origin+location.pathname});
  try{
   if(task.resume_url){
    if(location.origin+location.pathname!==task.resume_url)throw Error('续传页面与原聊天不一致，已停止');
   }else{
    if(/\/c\//.test(location.pathname))throw Error('导入需要一个空白新聊天，当前页面已存在对话');
    await enterWork(task);
   }
   await until(()=>editor(),'未找到输入框，请确认网页 Work 已加载完成',60000);
   if(task.resume_pending_marker){
    await until(()=>userContains(task.resume_pending_marker),'未找到上次待确认消息的校验码，请先核对原聊天；尚未重复发送',15000);
    if(task.resume_pending_phase==='finalization')finalSent=true;
    else sent=Math.min(sent+5,task.files.length);
    await message(task,'bridge:progress',{message:'已从原聊天确认上次待核对消息，继续后续步骤',...checkpoint()});
   }
   for(let start=sent;start<task.files.length;start+=5){
    await until(()=>!stopped(),'上一条回复尚未结束，已暂停导入');
    await message(task,'bridge:progress',{message:'正在导入「'+task.title+'」：文件 '+(start+1)+' / '+task.files.length,sent_files:sent,chat_url:/\/c\//.test(location.pathname)?location.origin+location.pathname:''});
    const group=task.files.slice(start,start+5);
    const transfer=new DataTransfer();
    for(let i=0;i<group.length;i++)transfer.items.add(await loadFile(task,start+i,group[i]));
    const input=await uploadInput();
    await message(task,'bridge:restore-check');
    const names=Array.from(transfer.files,file=>file.name);
    input.files=transfer.files;
    input.dispatchEvent(new Event('change',{bubbles:true}));
    const text='历史对话名称：'+task.title+'\n资料批次：'+(Math.floor(start/5)+1)+' / '+Math.ceil(task.files.length/5)+
      '\n本次上传文件：\n'+names.join('\n')+
      '\n资料正在分批上传，请只确认本批收到的文件，等待单独的“本条资料已上传结束”消息后再整理。';
    await message(task,'bridge:progress',{message:'「'+task.title+'」附件已放入网页，正在核对上传状态',...checkpoint()});
    await until(()=>{
     const scope=composerAttachments(names);
     return scope&&!Array.from(scope.querySelectorAll('[role="progressbar"],[aria-busy="true"]')).some(visible);
    },'附件未获网页确认，尚未发送。请检查附件卡片、上传错误或额度提示');
    await sendTurn(task,text,async()=>{
     sent+=group.length;
     await message(task,'bridge:progress',{message:'「'+task.title+'」已发送 '+sent+' / '+task.files.length+' 个文件，等待本批回复',...checkpoint()});
    },start+group.length<task.files.length);
   }
   phase='finalization';
   await message(task,'bridge:progress',{message:'「'+task.title+'」文件已发送，正在核对收尾状态',...checkpoint()});
   if(!finalSent)await sendTurn(task,task.prompt+'\n\n本条资料已上传结束。历史对话：'+task.title+
    '\n本条共发送 '+sent+' 个文件，清单如下：\n'+task.files.map(file=>file.path.split('/').pop()).join('\n')+
    '\n现在请开始核对实际收到并能读取的资料，明确列出缺失项，然后整理本条对话的进度。',async()=>{
     finalSent=true;
     await message(task,'bridge:progress',{message:'「'+task.title+'」收尾指令已送达，模型回复可在聊天中核对',...checkpoint()});
    },false);
   phase='rename';
   let renameError='';
   try{await renameConversation(task.title,()=>message(task,'bridge:restore-check'));}
   catch(error){assertActive();renameError=error.message;}
   phase='complete';
   await message(task,'bridge:done',{...checkpoint(),
    rename_status:renameError?'unconfirmed':'confirmed',rename_error:renameError,
    message:'「'+task.title+'」的 '+sent+' 个文件及收尾指令已发送，模型回复请在聊天核对。'+(renameError?'名称未确认：'+renameError:'对话名称已保存。')});
  }catch(error){
   await chrome.runtime.sendMessage({type:'bridge:done',id:task.id,error:error.message,...checkpoint(),submission_uncertain:error.submissionUncertain===true}).catch(()=>{});
  }
 }
 root.MemoryBridgeRestore={importConversation,renameConversation,composerAttachments,stop(){cancelled=true;}};
})(globalThis);
