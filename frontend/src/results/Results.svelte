<script lang="ts">
  import { onDestroy } from 'svelte';
  import { getSession } from '../application';
  import SavedWorkspace from './SavedWorkspace.svelte';
  const session = getSession();
  let authority = $state.raw(session.state);
  const unsubscribe = session.subscribe((value) => (authority = value));
  onDestroy(unsubscribe);
</script>

<!-- Catalog-scoped handles and drafts cannot outlive their authority/revision. -->
{#key `${authority.sessionId}/${authority.catalogRevision ?? ''}`}<SavedWorkspace />{/key}
