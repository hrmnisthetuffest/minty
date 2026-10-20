// Runs one page at a time. A page started by an older run() that finishes after a newer
// run() or stop() is cleaned up immediately and reported stale, so it can never leak timers
// or subscriptions.
export function createRunner() {
  let generation = 0;
  let cleanup = null;

  function stop() {
    generation += 1;
    const fn = cleanup;
    cleanup = null;
    if (fn) fn();
  }

  // Resolves true when `start` is still the current run, false when it was superseded.
  // Errors from a superseded run are dropped; errors from the current run propagate.
  async function run(start) {
    stop();
    const mine = generation;
    let result;
    try {
      result = await start();
    } catch (e) {
      if (mine !== generation) return false;
      throw e;
    }
    if (mine !== generation) {
      if (typeof result === "function") result();
      return false;
    }
    cleanup = typeof result === "function" ? result : null;
    return true;
  }

  return { run, stop };
}
