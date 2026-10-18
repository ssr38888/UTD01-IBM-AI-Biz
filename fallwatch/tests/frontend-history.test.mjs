import {test} from 'node:test';
import assert from 'node:assert/strict';
import {advanceHistoryCursor} from '../frontend/history-data.mjs';

test('a refresh with more than one page of arrivals preserves the missing gap',()=>{
  const result=advanceHistoryCursor({currentCursor:51,previousTop:100,previousCount:50,
    page:{events:[{seq:160},{seq:111}],next_cursor:111}});
  assert.equal(result,111);
});
test('new arrivals reopen pagination even when previous history was fully loaded',()=>{
  assert.equal(advanceHistoryCursor({currentCursor:null,previousTop:100,previousCount:100,
    page:{events:[{seq:160},{seq:111}],next_cursor:111}}),111);
});
test('overlapping refresh retains the deeper cursor; explicit append advances it',()=>{
  const input={currentCursor:51,previousTop:100,previousCount:50,page:{events:[{seq:105},{seq:56}],next_cursor:56}};
  assert.equal(advanceHistoryCursor(input),51);
  assert.equal(advanceHistoryCursor({...input,append:true}),56);
});
