const test=require('node:test'), assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../extension/page-adapter.js'),'utf8');
// Minimal visible-DOM fixture. Browser compatibility is tested separately on the real site.
class E {
 constructor(tag,attrs={},children=[]){this.tagName=tag.toUpperCase();this.nodeType=1;this.attrs=attrs;this.childNodes=children;this.children=children.filter(n=>n.nodeType===1);for(const n of children)n.parentElement=this;this.events=[];this.order=0;}
 get textContent(){return this.childNodes.map(n=>n.textContent).join('');}
 set textContent(value){this.childNodes=[T(value)];this.children=[];}
 get innerText(){return this.textContent;}
 getAttribute(name){return this.attrs[name]??null;}
 getClientRects(){return this.attrs.display==='contents'||this.attrs.hidden||this.attrs.display==='none'?[]:[{}];}
 matches(selector){return selector.split(',').some(s=>{s=s.trim();return s.toUpperCase()===this.tagName||s==='h4[data-conversation-role]'&&this.tagName==='H4'&&!!this.attrs['data-conversation-role']||s==='[hidden]'&&!!this.attrs.hidden||s==='[aria-hidden="true"]'&&this.attrs['aria-hidden']==='true'||s.startsWith('[data-testid*=')&&String(this.attrs['data-testid']).includes(s.includes('thinking')?'thinking':'reasoning')||s==='.thinking-block'&&this.attrs.class==='thinking-block';});}
 closest(selector){for(let n=this;n;n=n.parentElement)if(n.matches(selector))return n;return null;}
 compareDocumentPosition(other){return this.order<other.order?4:2;}
 querySelectorAll(selector){const all=this.children.flatMap(n=>[n,...n.querySelectorAll(selector)]);return all.filter(n=>selector==='a[href]'?n.tagName==='A':false);}
 focus(){}
 dispatchEvent(event){this.events.push(event.type);}
}
const T=value=>({nodeType:3,textContent:value});
class TA extends E {get value(){return this.draft||'';}set value(value){this.draft=value;}}
function fixture(){
 const user=new E('div',{display:'contents'},[new E('h4',{'data-conversation-role':'user'},[T('You said:')]),new E('p',{},[T('Who designed the Analytical Engine?')])]);user.order=1;
 const answer=new E('div',{},[new E('h4',{'data-conversation-role':'assistant'},[T('ChatGPT said:')]),new E('p',{},[T('Designed by '),new E('button',{},[T('Charles Babbage')]),T('.')]),new E('button',{},[T('Copy')]),new E('div',{'data-testid':'thinking-panel'},[T('hidden internal text')])]);answer.order=2;
 const input=new TA('textarea');
 const context={Element:E,HTMLTextAreaElement:TA,Node:{DOCUMENT_POSITION_FOLLOWING:4},Event:class{constructor(type){this.type=type;}},URL,Date,Set,clearTimeout,setTimeout,getComputedStyle:n=>({display:n.attrs.display||'block',visibility:'visible'}),location:{hostname:'chatgpt.com',origin:'https://chatgpt.com',pathname:'/test'},window:{getSelection:()=>''},document:{querySelectorAll:s=>s.includes('assistant')?[answer.childNodes[0]]:s.includes('user')?[user.childNodes[0]]:s.includes('textarea')?[input]:[],querySelector:()=>null}};
 vm.createContext(context);vm.runInContext(code,context);return {api:context.evPage,input,context};
}
test('display:contents question and inline entity buttons survive, headings and controls do not',()=>{const {api}=fixture(),s=api.run('capture');assert.equal(s.question,'Who designed the Analytical Engine?');assert.equal(s.candidate,'Designed by Charles Babbage .');assert(!s.candidate.includes('Copy'));assert(!s.candidate.includes('hidden internal'));});
test('draft insertion emits input, never sends, and refuses to overwrite',()=>{const {api,input}=fixture();assert.equal(api.run('insert','audit prompt').sent,false);assert.equal(input.value,'audit prompt');assert.deepEqual(input.events,['input']);assert.throws(()=>api.run('insert','replacement'),/已有草稿/);assert.equal(input.value,'audit prompt');});
test('ambiguous visible editors are rejected',()=>{const f=fixture();f.context.document.querySelectorAll=s=>s.includes('textarea')?[f.input,new TA('textarea')]:[];assert.throws(()=>f.api.run('insert','prompt'),/找不到输入框/);});
