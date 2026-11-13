'use strict';
importScripts('desktop.js');
// Only download URLs explicitly observed in message DOM. Never discover endpoints.
chrome.runtime.onMessage.addListener((request, sender, respond) => {
  if (request?.type !== 'bridge:asset') return;
  (async () => {
    if (sender.id !== chrome.runtime.id || !/^https:\/\/chatgpt\.com\//.test(sender.url || '')) throw Error('无效页面来源');
    const url = new URL(request.url);
    const searchImage = url.hostname === 'images.openai.com';
    if (url.protocol !== 'https:' || !(searchImage || url.hostname === 'chatgpt.com' || url.hostname.endsWith('.oaiusercontent.com') || url.hostname.endsWith('.oaistatic.com'))) throw Error('此资源域名未授权；已保留引用，请手动下载');
    const response = await fetch(url.href, {credentials:searchImage?'omit':'include',...(searchImage?{redirect:'error'}:{}),signal:AbortSignal.timeout(25000)});
    if (!response.ok) throw Error('下载返回 HTTP '+response.status);
    const mime = (response.headers.get('content-type') || '').split(';')[0];
    if (/text\/html|application\/json/.test(mime)) throw Error('返回登录页或错误数据，未作为附件保存');
    const reader = response.body.getReader(); const chunks=[];let total=0;
    try { while (true) { const {done,value}=await reader.read(); if(done)break; total+=value.length;if(total>25*1024*1024)throw Error('文件超过 25 MiB，请手动下载');chunks.push(value); } } finally { await reader.cancel().catch(()=>{}); }
    const bytes = new Uint8Array(total);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}
    let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));
    return {ok:true,base64:btoa(binary),mime};
  })().then(respond,e=>respond({ok:false,error:e.message}));
  return true;
});
