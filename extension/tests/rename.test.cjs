const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
test('renames only the current conversation through its menu and verifies the saved title',async()=>{
 const dom=new JSDOM('<aside><div><a href="/c/other">其他对话</a><button aria-label="更多选项">…</button></div><div><a href="/c/current">临时标题</a><button id="options" aria-label="更多选项">…</button></div></aside>',{
  url:'https://chatgpt.com/c/current',runScripts:'outside-only'});
 const w=dom.window;
 w.document.querySelector('#options').onclick=()=>{
  const item=w.document.createElement('button');item.setAttribute('role','menuitem');item.textContent='重命名';w.document.body.append(item);
  item.onclick=()=>{
   item.remove();const dialog=w.document.createElement('div');dialog.setAttribute('role','dialog');
   dialog.innerHTML='<input type="text"><button>保存</button>';w.document.body.append(dialog);
   dialog.querySelector('button').onclick=()=>{
    w.document.querySelector('a[href="/c/current"]').textContent=dialog.querySelector('input').value;dialog.remove();
   };
  };
 };
 w.eval(fs.readFileSync(path.join(__dirname,'../restore.js'),'utf8'));
 try{
  await w.MemoryBridgeRestore.renameConversation('日语学习・复习');
  assert.equal(w.document.querySelector('a[href="/c/current"]').textContent,'日语学习・复习');
  assert.equal(w.document.querySelector('a[href="/c/other"]').textContent,'其他对话');
 }finally{w.close();}
});
test('does not rename an unrelated sidebar conversation when the current one is absent',async()=>{
 const dom=new JSDOM('<a href="/c/other">其他对话</a>',{url:'https://chatgpt.com/c/current',runScripts:'outside-only'});
 const w=dom.window;w.eval(fs.readFileSync(path.join(__dirname,'../restore.js'),'utf8'));
 try{await assert.rejects(w.MemoryBridgeRestore.renameConversation('新标题'),/未出现在侧边栏/);}
 finally{w.close();}
});
