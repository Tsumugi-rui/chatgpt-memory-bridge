const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function service(fetch){let listener;vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../background.js'),'utf8'),{importScripts:()=>{},chrome:{runtime:{id:'test-extension',onMessage:{addListener:f=>listener=f}}},URL,AbortSignal,Uint8Array,String,btoa,fetch});return request=>new Promise(resolve=>listener({type:'bridge:asset',...request},{id:'test-extension',url:'https://chatgpt.com/c/test'},resolve));}
test('fetches only an observed supported HTTPS asset and returns bytes',async()=>{
 let called;const send=service(async(url,options)=>{called={url,options};return new Response(new Uint8Array([137,80,78,71]),{headers:{'content-type':'image/png'}});});
 const r=await send({url:'https://files.oaiusercontent.com/picture.png'});assert.equal(r.ok,true);assert.equal(r.mime,'image/png');assert.equal(atob(r.base64).length,4);assert.equal(called.options.credentials,'include');
});
test('rejects external hosts and non-HTTPS URLs without contacting them',async()=>{
 const send=service(()=>{throw Error('must not fetch');});
 for(const url of ['https://evil.example/file.png','http://chatgpt.com/file','file:///C:/secret']){const r=await send({url});assert.equal(r.ok,false);assert.match(r.error,/域名|授权/);}
});
test('allows OpenAI search images without including credentials or following redirects',async()=>{
 let called;
 const send=service(async(url,options)=>{called={url,options};return new Response(new Uint8Array([137,80,78,71]),{headers:{'content-type':'image/png'}});});
 const r=await send({url:'https://images.openai.com/thumbnails/example.png'});
 assert.equal(r.ok,true);
 assert.equal(called.options.credentials,'omit');
 assert.equal(called.options.redirect,'error');
});
test('expired attachments and login pages are reported as failures',async()=>{
 for(const response of [new Response('expired',{status:403}),new Response('<html>login</html>',{headers:{'content-type':'text/html'}}),new Response('{}',{headers:{'content-type':'application/json'}})]){
  const r=await service(async()=>response)({url:'https://chatgpt.com/observed-file'});assert.equal(r.ok,false);
 }
});
