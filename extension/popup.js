'use strict';
document.querySelector('#open').addEventListener('click', async () => {
  const status = document.querySelector('#status');
  try {
    const ready=await chrome.runtime.sendMessage({type:'bridge:ready'});
    if(!ready?.ok)throw Error(ready?.error||'插件缓存初始化失败，请重新加载插件。');
    const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
    if (!/^https:\/\/chatgpt\.com\/(c|share)\/[a-zA-Z0-9-]+/.test(tab?.url || '')) throw Error('请先打开 chatgpt.com 上的一条具体对话。');
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['core.js','zip.js','volumes.js','store.js','collector.js']});
    status.textContent = '面板已打开，请在网页右下角点击“开始完整采集”。';
  } catch (e) { status.textContent = e.message; }
});
document.querySelector('#pair').addEventListener('click',async()=>{
 const status=document.querySelector('#status');status.textContent='正在连接…';
 try{const result=await chrome.runtime.sendMessage({type:'bridge:pair',code:document.querySelector('#code').value.trim()});if(!result?.ok)throw Error(result?.error||'连接失败');status.textContent='已连接！回到软件读取列表并选择对话。';}
 catch(e){status.textContent=e.message+'。请先打开新版软件的“连接插件”。';}
});
