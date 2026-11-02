const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../static/sidecar.js'),'utf8');
function harness(result){
 const nodes=new Map(),requests=[];
 const node=()=>({value:'',hidden:false,disabled:false,textContent:'',children:[],append(n){this.children.push(n)},replaceChildren(){this.children=[]},addEventListener(){}});
 const get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
 const context={document:{getElementById:get,createElement:node},location:{protocol:'http:',origin:'http://127.0.0.1:8790'},AbortSignal,crypto:require('node:crypto').webcrypto,setTimeout,console,fetch:async(url,opts)=>{
  requests.push({url,body:opts.body?JSON.parse(opts.body):null,method:opts.method});
  if(url.endsWith('/api/sidecar/plans')&&opts.method==='POST')return {ok:true,json:async()=>({id:'plan-existing',state:'queued'})};
  if(url.endsWith('/api/plan-proposals/plan-existing')&&opts.method==='GET')return {ok:true,json:async()=>result};
  throw Error('Unexpected request path; no paid service is used');
 }};
 vm.createContext(context);vm.runInContext(code,context);
 get('question').value='Who designed X?';get('candidate').value='Nora';get('evidence').value='[{"id":"E","text":"source only"}]';get('relations').value='[]';
 return {get,requests};
}
test('plan submission and observation use their distinct API routes, without evidence in planning',async()=>{
 const f=harness({id:'plan-existing',state:'completed',result:{status:'ok',proposal:{relations:[{subject:'X',predicate:'designed_by',object:'Nora'}]}}});
 await f.get('plan').onclick();
 assert.equal(f.requests.length,2);assert(f.requests[1].url.endsWith('/api/plan-proposals/plan-existing'));
 assert.deepEqual(f.requests[0].body,{question:'Who designed X?',candidate:'Nora'});
 assert.equal(JSON.parse(f.get('relations').value)[0].object,'Nora');assert.equal(f.get('error').hidden,true);
});
test('failed planning does not produce a success stage or enable a revision prompt',async()=>{
 const f=harness({id:'plan-existing',state:'failed',error:'invalid source span'});
 await f.get('plan').onclick();
 assert.equal(f.get('relations').value,'[]');assert.equal(f.get('prompt').disabled,true);assert.equal(f.get('error').hidden,false);assert.equal(f.get('stages').children.length,0);
});
