/** Keep a native popover anchored through scrolling, resizing and content changes. */
export function anchorPopover(panel: HTMLElement, anchor: () => HTMLElement | undefined) {
  let watching = false;
  const position = () => {
    const button = anchor();
    if (!button || !panel.matches(':popover-open')) return;
    const rect = button.getBoundingClientRect(),
      bounds = panel.getBoundingClientRect();
    panel.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - bounds.width - 8))}px`;
    const below = rect.bottom + 6,
      above = rect.top - bounds.height - 6;
    panel.style.top = `${Math.max(8, Math.min(below + bounds.height <= innerHeight - 8 ? below : above, innerHeight - bounds.height - 8))}px`;
  };
  const observer = new ResizeObserver(position);
  const stop = () => {
    watching = false;
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
    observer.disconnect();
  };
  const toggle = () => {
    stop();
    if (!panel.matches(':popover-open')) return;
    watching = true;
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, { capture: true, passive: true });
    observer.observe(panel);
    const button = anchor();
    if (button) observer.observe(button);
    position();
  };
  panel.addEventListener('toggle', toggle);
  return {
    update(next: typeof anchor) {
      anchor = next;
      if (watching) position();
    },
    destroy() {
      stop();
      panel.removeEventListener('toggle', toggle);
    },
  };
}
