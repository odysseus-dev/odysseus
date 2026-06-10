'use strict';

const assert = require('assert');
const checkPrDescription = require('./check-pr-description');

function completeBody() {
  return [
    '## Summary',
    'Adds a first-time contributor guidance comment for new pull request authors.',
    '',
    '## Linked Issue',
    'Fixes #2109',
    '',
    '## Type of Change',
    '- [x] CI / tooling / configuration',
    '',
    '## Checklist',
    '- [x] I searched open issues and open PRs — this is not a duplicate.',
    '',
    '## How to Test',
    '1. Run the PR description check unit tests.',
  ].join('\n');
}

function makeHarness({ authoredItems = [], comments = [] } = {}) {
  const calls = [];
  const rest = {
    issues: {
      listComments: function listComments() {},
      listForRepo: function listForRepo() {},
      createComment: async args => calls.push({ method: 'createComment', args }),
      deleteComment: async args => calls.push({ method: 'deleteComment', args }),
      updateComment: async args => calls.push({ method: 'updateComment', args }),
      getLabel: async () => ({}),
      addLabels: async args => calls.push({ method: 'addLabels', args }),
      removeLabel: async args => calls.push({ method: 'removeLabel', args }),
    },
  };

  const github = {
    rest,
    paginate: async fn => {
      if (fn === rest.issues.listComments) return comments;
      if (fn === rest.issues.listForRepo) return authoredItems;
      throw new Error('unexpected paginate target');
    },
  };

  const context = {
    repo: { owner: 'pewdiepie-archdaemon', repo: 'odysseus' },
    payload: {
      pull_request: {
        number: 42,
        body: completeBody(),
        user: { login: 'newcontrib' },
      },
    },
  };

  return { github, context, core: { warning() {}, setFailed(msg) { calls.push({ method: 'setFailed', msg }); } }, calls };
}

async function testFirstTimeContributorGetsGuideComment() {
  const harness = makeHarness({ authoredItems: [{ number: 42, pull_request: {} }] });
  await checkPrDescription(harness);

  const comment = harness.calls.find(call => call.method === 'createComment'
    && call.args.body.includes('<!-- first-time-contributor-guide-bot -->'));
  assert(comment, 'expected a welcome guide comment for first-time PR author');
  assert(comment.args.body.includes('CONTRIBUTING.md#pull-requests'));
  assert(comment.args.body.includes('.github/pull_request_template.md'));
}

async function testReturningContributorDoesNotGetGuideComment() {
  const harness = makeHarness({ authoredItems: [
    { number: 41, pull_request: {} },
    { number: 42, pull_request: {} },
  ] });
  await checkPrDescription(harness);

  const welcomeComments = harness.calls.filter(call => call.method === 'createComment'
    && call.args.body.includes('<!-- first-time-contributor-guide-bot -->'));
  assert.strictEqual(welcomeComments.length, 0, 'returning PR author should not get welcome guide comment');
}

async function testExistingGuideCommentIsNotDuplicated() {
  const harness = makeHarness({
    authoredItems: [{ number: 42, pull_request: {} }],
    comments: [{ id: 1, body: '<!-- first-time-contributor-guide-bot -->\nThanks!' }],
  });
  await checkPrDescription(harness);

  const welcomeComments = harness.calls.filter(call => call.method === 'createComment'
    && call.args.body.includes('<!-- first-time-contributor-guide-bot -->'));
  assert.strictEqual(welcomeComments.length, 0, 'existing guide comment should not be duplicated');
}

(async () => {
  await testFirstTimeContributorGetsGuideComment();
  await testReturningContributorDoesNotGetGuideComment();
  await testExistingGuideCommentIsNotDuplicated();
  console.log('check-pr-description first-time contributor tests passed');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
