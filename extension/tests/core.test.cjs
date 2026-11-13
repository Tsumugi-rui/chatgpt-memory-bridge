'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const C=require('../core.js');
const m=(id,text,role='assistant')=>({id,text,role,html:text});
const snap=messages=>({messages,index:0});
test('joins overlapping virtualized windows and keeps order',()=>{
 const r=C.assemble([snap([m('a','一'),m('b','二'),m('c','三')]),snap([m('b','二'),m('c','三'),m('d','四')])]);
 assert.equal(r.gaps,0);assert.deepEqual(r.messages.map(x=>x.id),['a','b','c','d']);
});
test('backtracking bridges a gap later',()=>{
 const r=C.assemble([snap([m('a','a'),m('b','b')]),snap([m('d','d'),m('e','e')]),snap([m('b','b'),m('c','c'),m('d','d')])]);
 assert.equal(r.gaps,0);assert.deepEqual(r.messages.map(x=>x.id),['a','b','c','d','e']);
});
test('identical short replies with different IDs remain separate',()=>{
 const r=C.assemble([snap([m('u1','继续','user'),m('a1','好的'),m('u2','继续','user'),m('a2','好的')]),snap([m('u2','继续','user'),m('a2','好的'),m('u3','继续','user'),m('a3','好的')])]);
 assert.equal(r.messages.length,6);assert.equal(r.gaps,0);
});
test('no-ID repeated content is ambiguous and must not silently collapse',()=>{
 assert.equal(C.align([m(null,'继续','user'),m(null,'是'),m(null,'继续','user'),m(null,'是')],[m(null,'继续','user'),m(null,'是')]),null);
});
test('partially rendered trailing user links through assistant anchor',()=>{
 const r=C.assemble([snap([m('a','前文'),m(null,'继续','user')]),snap([m('a','前文'),m('user-before:b','继续','user'),m('b','下一课')])]);
 assert.equal(r.messages.length,3);assert.equal(r.messages[1].id,'user-before:b');
});
test('ignores an empty user placeholder between the same two IDs and keeps the attachment',()=>{
 const attachment={...m(null,'[图片]','user'),assets:[{kind:'image',url:'blob:synthetic'}]};
 const content=snap([m('a','前文'),attachment,m('b','后文')]);
 const placeholder=snap([m('a','前文'),m(null,'','user'),m('b','后文')]);
 for(const order of [[content,placeholder],[placeholder,content]]){
  const result=C.assemble(order);
  assert.equal(result.gaps,0);
  assert.equal(result.messages.length,3);
  assert.deepEqual(result.messages[1].assets,attachment.assets);
 }
 assert.equal(C.align(content.messages, [m('a','前文'),m(null,'','user'),m('x','另一分支')]),null);
});
test('modified text of same message keeps original evidence',()=>{
 const r=C.assemble([snap([m('a','旧内容')]),snap([m('a','新内容')])]);
 assert.equal(r.messages[0].text,'新内容');assert.equal(r.messages[0].versions[0].text,'旧内容');
});
test('unconnected snippets remain visibly separate',()=>{
 const r=C.assemble([snap([m('a','旧')]),snap([m('z','新')])]);assert.equal(r.gaps,1);assert.match(C.markdown({title:'标题',url:'https://chatgpt.com/c/x'},r),/未连接/);
});
test('one unanchored user reply cannot prove an entire conversation boundary',()=>{
 const r=C.assemble([snap([m('a','前一课'),m(null,'tanaka san no hon desu','user')]),snap([m(null,'tanaka san no hon desu','user'),m('u3','继续昨天的学习','user'),m('b','下一课')])]);
 assert.equal(r.gaps,1);assert.equal(r.messages.length,5);
});
test('consecutive user messages are preserved rather than forced into pairs',()=>{
 const r=C.assemble([snap([m('u1','第一条','user'),m('u2','补充一下','user'),m('a','明白')])]);assert.equal(r.messages.length,3);assert.deepEqual(r.messages.map(m=>m.role),['user','user','assistant']);
});
test('HTML text is escaped',()=>assert.equal(C.escape('<script>&"'), '&lt;script&gt;&amp;&quot;'));


test('export filename includes readable title, local date with timezone and conversation ID',()=>{
 const date={getFullYear:()=>2026,getMonth:()=>9,getDate:()=>29,getHours:()=>22,getMinutes:()=>30,getSeconds:()=>5,getMilliseconds:()=>123,getTimezoneOffset:()=>-480};
 const name=C.exportName('日语学习 / 第一课? - ChatGPT','https://chatgpt.com/c/abc-123',date);
 assert.equal(name,'ChatGPT__日语学习 _ 第一课___2026-10-29_22-30-05-123_UTC+0800__abc-123.zip');
 assert.doesNotMatch(name,/[<>:"/\\|?*\x00-\x1f]/);
});

test('filename tolerates missing and long Unicode titles without cutting a surrogate pair',()=>{
 const one=C.exportName('😀'.repeat(100)+'数学','https://chatgpt.com/c/abc');
 assert.ok(one.startsWith('ChatGPT__'+'😀'.repeat(24)+'__'));
 assert.ok(!one.includes('\ufffd'));assert.ok(one.length<160);
 assert.match(C.exportName('...','https://chatgpt.com/c/abc'),/未命名对话/);
 assert.notEqual(C.exportName('同名','https://chatgpt.com/c/a'),C.exportName('同名','https://chatgpt.com/c/b'));
});
