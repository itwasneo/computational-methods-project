import type { WireTypes } from './wire-types';

/** Route, request and response cannot be chosen independently by callers. */
export const endpoints = {
  '/runs/capabilities': ['RunContext', 'ExecutionCapabilities'],
  '/instances/generate': ['InstanceGenerateRequest', 'StoredInstance'],
  '/instances/load': ['InstanceLoadRequest', 'StoredInstance'],
  '/instances/snapshot': ['SnapshotRequest', 'StoredInstance'],
  '/runs/preflight': ['ExecutionPreflightRequest', 'ReviewedExecution'],
  '/jobs/query': ['JobsQueryRequest', 'JobsPage'],
  '/jobs/submit': ['JobSubmitRequest', 'JobSubmission'],
  '/jobs/review': ['JobStatusRequest', 'JobReview'],
  '/jobs/control': ['ExecutionControlRequest', 'JobEnvelope'],
  '/jobs/analysis': ['JobAnalysisRequest', 'JobAnalysisResponse'],
  '/jobs/result': ['JobPublicationRequest', 'JobDetailsResponse'],
  '/jobs/geometry': ['JobGeometryRequest', 'JobGeometryResponse'],
  '/queue/status': ['RunContext', 'QueueStatus'],
  '/queue/continue': ['QueueContinueRequest', 'QueueStatus'],
} as const satisfies Record<string, readonly [keyof WireTypes, keyof WireTypes]>;
export type Endpoint = keyof typeof endpoints;
export type Input<P extends Endpoint> = Omit<
  WireTypes[(typeof endpoints)[P][0]],
  'api_version' | 'request_id'
>;
export type Output<P extends Endpoint> = WireTypes[(typeof endpoints)[P][1]];
export type Parameters<P extends Endpoint> = Omit<Input<P>, 'workspace_id' | 'session_id'>;
