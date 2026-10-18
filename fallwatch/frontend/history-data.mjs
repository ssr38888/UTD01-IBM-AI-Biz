export function advanceHistoryCursor({currentCursor,previousTop,previousCount,page,append=false}) {
  const gap = page.events.length > 0 && page.events.at(-1).seq > previousTop;
  return append || !previousCount || gap ? page.next_cursor : currentCursor;
}
