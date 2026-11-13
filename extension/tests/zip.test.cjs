const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),cp=require('node:child_process');
const Z=require('../zip.js');
test('UTF-8 ZIP can be verified and read by Python independently',async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-zip-'));const file=path.join(tmp,'sample.zip');
 try{fs.writeFileSync(file,Buffer.from(await Z.zip([{name:'中文/日本語.md',data:'あいうえお\n你好 🌷'},{name:'assets/picture.bin',data:new Uint8Array([0,255,7,10])}]).arrayBuffer()));
 const result=cp.spawnSync('python',['-c','import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);assert z.testzip() is None;assert z.read("中文/日本語.md").decode()=="あいうえお\\n你好 🌷";assert z.read("assets/picture.bin")==bytes([0,255,7,10])',file],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
 }finally{assert.ok(path.resolve(tmp).startsWith(path.resolve(os.tmpdir())+path.sep));assert.ok(path.basename(tmp).startsWith('bridge-zip-'));fs.rmSync(tmp,{recursive:true,force:true});}
});
test('rejects paths outside the archive',()=>assert.throws(()=>Z.zip([{name:'../escape',data:'x'}]),/路径/));
test('CRC32 standard vector',()=>assert.equal(Z.crc(new TextEncoder().encode('123456789')),0xcbf43926));
