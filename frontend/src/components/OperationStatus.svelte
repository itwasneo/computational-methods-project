<script lang="ts">
  import type { OperationState } from '../experiments/controller';
  let { operation, retry }: { operation: OperationState; retry?: () => unknown } = $props();
</script>

{#if operation.busy}<p role="status">{operation.busy}…</p>{/if}
{#if operation.error}
  <div class="error-panel" role="alert">
    <p>{operation.error}</p>
    {#if retry}<button disabled={!!operation.busy} onclick={retry}>
        Retry this operation
      </button>{/if}
    {#if operation.requestId}<details>
        <summary>Request details</summary>
        <p>Code: {operation.code}</p>
        <p>Request: {operation.requestId}</p>
      </details>{/if}
  </div>
{/if}
