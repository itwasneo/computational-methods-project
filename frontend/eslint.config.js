import js from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import ts from 'typescript-eslint';
import globals from 'globals';

export default [
  { ignores: ['dist/**', 'src/wire-types.ts', 'src/wire-schema.json'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs['flat/recommended'],
  { languageOptions: { globals: { ...globals.browser, ...globals.node } } },
  { files: ['**/*.svelte'], languageOptions: { parserOptions: { parser: ts.parser } } },
  {
    files: ['**/*.mjs'],
    rules: { '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }] },
  },
];
