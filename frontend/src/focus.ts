/** Only deliberate navigation moves focus; honor reduced motion and avoid hidden-view focus theft. */
export function reveal(element: HTMLElement | undefined) {
  if (!element || document.visibilityState !== 'visible' || !element.checkVisibility()) return;
  element.focus({ preventScroll: true });
  element.scrollIntoView({
    block: 'start',
    behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
  });
}
