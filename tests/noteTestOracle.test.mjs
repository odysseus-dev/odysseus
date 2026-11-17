import test from 'node:test';
import assert from 'node:assert/strict';
import {expectedNoteTitles,compareNoteState} from '../scripts/note_test_oracle.mjs';
import {scoreCalls} from '../scripts/compare_schema_thinking.mjs';
const rows=[{id:'a123',title:'Japan',content:'body',pinned:false},{id:'b123',title:'Today',content:'keep'}];
const call=args=>({function:{name:'manage_notes',arguments:JSON.stringify(args)}});
test('shared expectations cover negation, exceptions and invalid cases',()=>{
  assert.deepEqual(expectedNoteTitles('keep_all',['Japan','Today']),[]);
  assert.deepEqual(expectedNoteTitles('contrast',['Groceries','Japan','Today']),['Groceries']);
  assert.deepEqual(expectedNoteTitles('except_one',['Groceries','Japan','Today']),['Groceries','Today']);
  assert.throws(()=>expectedNoteTitles('unregistered',[]));
});
test('state oracle detects edits and additions, not just disappearing IDs',()=>{
  assert.equal(compareNoteState(rows,rows).unchanged,true);
  assert.equal(compareNoteState(rows,[rows[1]],['a123']).exact,true);
  assert.equal(compareNoteState(rows,[{...rows[1],content:'changed'}],['a123']).exact,false);
  assert.deepEqual(compareNoteState(rows,[{...rows[0],pinned:true},rows[1]]).changed_fields,['pinned']);
  assert.equal(compareNoteState(rows,[...rows,{id:'new',title:'extra'}]).added_count,1);
  assert.equal(compareNoteState(rows,[{...rows[0],archived:true},rows[1]],['a123']).exact,false);
});
test('duplicate deletion cannot pass exact proposal check',()=>{
  const result=scoreCalls([call({action:'delete',id:'a123'}),call({action:'delete',title:'Japan'})],rows,['Japan']);
  assert.equal(result.exact_target_proposal,false);
  assert.equal(result.duplicate_targets,1);
});
test('stale ID with valid unique title follows actual backend fallback',()=>{
  assert.equal(scoreCalls([call({action:'delete',id:'stale',title:'Japan'})],rows,['Japan']).exact_target_proposal,true);
  assert.equal(scoreCalls([{function:{name:'manage_notes',arguments:'null'}}],rows,[]).exact_target_proposal,false);
});
