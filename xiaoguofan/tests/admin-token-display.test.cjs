const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

test('admin zero metered requests means unknown, not zero tokens',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../portal/static/admin-stats.js'),'utf8');
 const context=vm.createContext({$:()=>({})});
 vm.runInContext(source,context);
 assert.equal(vm.runInContext('adminTokenTotal({metered_requests:0,input_tokens:0,output_tokens:0})',context),'未报告');
 assert.equal(vm.runInContext('adminTokenTotal({metered_requests:1,input_tokens:0,output_tokens:0})',context),'0');
 assert.equal(vm.runInContext('adminTokenTotal({metered_requests:1,input_tokens:123,output_tokens:45})',context),'168');
 assert(source.includes('adminTokenTotal(u)'));
 assert(source.includes('adminTokenTotal(m)'));
});
