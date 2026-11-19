import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { palettes } from './rendering/webgl.mjs';

// Evaluate explicit opaque palette colors that automated DOM contrast scans cannot inspect inside WebGL.
const luminance = (rgb) =>
  rgb
    .slice(0, 3)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
const contrast = (a, b) =>
  (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);
const hex = (value) => {
  const digits = value.slice(1);
  const full = digits.length === 3 ? [...digits].map((c) => c + c).join('') : digits;
  return [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16) / 255);
};
test('viewer marks and tour have non-text contrast in both opaque theme palettes', () => {
  for (const [theme, palette] of Object.entries(palettes)) {
    for (const mark of ['point', 'selected', 'line'])
      assert.ok(contrast(palette[mark], palette.background) >= 3, `${theme} ${mark}`);
  }
});
test('text, focus, controls and chart series retain contrast in both CSS themes', () => {
  const css = readFileSync(new URL('./style.css', import.meta.url), 'utf8');
  const themes = [...css.matchAll(/:root(?:\[data-theme=["']dark["']\])?\s*\{([^}]+)\}/g)];
  assert.equal(themes.length, 2);
  for (const [, body] of themes) {
    const colors = Object.fromEntries(
      [...body.matchAll(/--([\w-]+):\s*(#[a-f0-9]{3,6});/g)].map(([, name, value]) => [
        name,
        hex(value),
      ]),
    );
    for (const name of ['text', 'muted'])
      assert.ok(contrast(colors[name], colors.page) >= 4.5, name);
    for (const name of ['control-border', 'focus', 'series-0', 'series-1', 'series-2', 'series-3'])
      assert.ok(contrast(colors[name], colors.page) >= 3, name);
    assert.ok(contrast(colors['viewer-text'], colors['viewer-toolbar']) >= 4.5);
  }
});
