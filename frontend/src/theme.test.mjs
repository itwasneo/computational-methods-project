import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveTheme } from './theme.ts';

test('explicit themes override the system; missing/invalid preferences follow it', () => {
  assert.equal(resolveTheme('light', true), 'light');
  assert.equal(resolveTheme('dark', false), 'dark');
  assert.equal(resolveTheme(null, true), 'dark');
  assert.equal(resolveTheme(null, false), 'light');
  assert.equal(resolveTheme('invalid', true), 'dark');
});
