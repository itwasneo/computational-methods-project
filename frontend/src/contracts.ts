// API v1 types and runtime schemas are generated from Python's authoritative DTOs.
// Static types do not validate responses or grant authority.
import type { WireTypes } from './wire-types';
export type * from './wire-types';
export const API_VERSION = 1;
export const PAGE_DEFAULT = 100;
export const PAGE_MAX = 500;
export const VALUES_PER_FILTER_MAX = 500;
export const SHARDS_MAX = 64;
export const RUN_LIST_MAX = 1000; // Wire ceiling, not an execution/resource allowance.
export const GEOMETRY_BYTES_MAX = 16 * 1024 * 1024;
export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type Field = WireTypes['ColumnFilter']['field'];
export type Scalar = WireTypes['ColumnFilter']['values'][number];
export type DistanceMode = WireTypes['ScientificConfiguration']['distance_modes'][number];
export type AnalysisQuery = WireTypes['JobAnalysisRequest']['query'];
