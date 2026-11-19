import definitions from './wire-schema.json' with { type: 'json' };
import type { WireTypes } from './wire-types';

type Schema =
  string | { ref?: string; literal?: unknown[]; union?: Schema[]; list?: Schema; map?: Schema };
const object = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === 'object' && !Array.isArray(v);
const schemas = definitions as Record<string, Record<string, Schema>>;
function matches(value: unknown, schema: Schema, depth = 0): boolean {
  if (depth > 64) return false;
  if (typeof schema === 'string') {
    switch (schema) {
      case 'null':
        return value === null;
      case 'integer':
        return typeof value === 'number' && Number.isSafeInteger(value);
      case 'number':
        return typeof value === 'number' && Number.isFinite(value);
      case 'json':
        return (
          value === null ||
          typeof value === 'string' ||
          typeof value === 'boolean' ||
          (typeof value === 'number' && Number.isFinite(value)) ||
          (Array.isArray(value) && value.every((v) => matches(v, 'json', depth + 1))) ||
          (object(value) && Object.values(value).every((v) => matches(v, 'json', depth + 1)))
        );
      default:
        return typeof value === schema;
    }
  }
  if (schema.ref) {
    const fields = schemas[schema.ref];
    return (
      object(value) &&
      !!fields &&
      Object.keys(value).length === Object.keys(fields).length &&
      Object.entries(fields).every(
        ([key, kind]) => Object.hasOwn(value, key) && matches(value[key], kind, depth + 1),
      )
    );
  }
  if (schema.literal) return schema.literal.some((v) => v === value);
  if (schema.union) return schema.union.some((s) => matches(value, s, depth + 1));
  if (schema.list)
    return Array.isArray(value) && value.every((v) => matches(v, schema.list!, depth + 1));
  if (schema.map)
    return object(value) && Object.values(value).every((v) => matches(v, schema.map!, depth + 1));
  return false;
}
export const wireGuard =
  <N extends keyof WireTypes>(name: N) =>
  (value: unknown): value is WireTypes[N] =>
    matches(value, { ref: name });
export type WireName = keyof typeof definitions;
