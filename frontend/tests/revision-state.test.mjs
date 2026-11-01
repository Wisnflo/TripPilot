import test from 'node:test';
import assert from 'node:assert/strict';
import {selectOutcome} from '../src/revision-state.js';
const original={status:'checked_ok',task:{nature_language:'预算400元'},plan:{people_number:1,itinerary:[]}};
test('failed and cancelled attempts retain the usable plan and its revision source',()=>{
 for(const status of ['request_error','hard_timeout','cancelled']){
  const attempt={status,plan:null,requests:[{kind:'error'}],billing_unknown:true};
  const result=selectOutcome(original,'accepted',attempt,'failed');
  assert.equal(result.active,'accepted');assert.equal(result.current.plan,original.plan);assert.equal(result.current.task.nature_language,'预算400元');assert.equal(result.current.lastAttempt,attempt);assert.equal(result.retained,true);
 }
 assert.equal(original.lastAttempt,undefined);
});
test('clarification and a successful new version remain actionable',()=>{
 const question={status:'needs_clarification',plan:null,question:'取消住宿吗？',before:original.plan};
 assert.equal(selectOutcome(original,'accepted',question,'question').active,'question');
 const accepted={status:'checked_ok',plan:{people_number:1,itinerary:[{day:1}]}};
 assert.equal(selectOutcome(original,'accepted',accepted,'new').current,accepted);
});
test('a first failed creation is not disguised as the visual demonstration',()=>{
 const failed={status:'request_error',plan:null};
 assert.equal(selectOutcome({...original,replay:true},null,failed,'failed').current,failed);
});
