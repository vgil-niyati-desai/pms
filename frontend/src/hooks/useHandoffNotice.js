import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

/**
 * A short confirmation that something worked, shown on the screen it worked on.
 *
 * Two things feed it. A screen can set its own message after an action it
 * handled itself, and a screen that *navigated* here can hand one over in the
 * route state — saving a project leaves the form and lands on the record, and
 * deleting one lands on the list, so in both cases the thing worth confirming
 * happened somewhere the user is no longer looking.
 *
 * It clears itself after `timeout` rather than waiting to be dismissed: a
 * confirmation is worth reading once, and a stale one still sitting there
 * later reads as a description of the current state rather than of something
 * that happened a minute ago. `tick` is what makes the same message twice in
 * a row restart the clock instead of inheriting the first one's.
 */
export default function useHandoffNotice(timeout = 6000) {
  const location = useLocation();
  const [notice, setNoticeState] = useState(location.state?.notice ?? null);
  const [tick, setTick] = useState(0);

  const setNotice = useCallback((message) => {
    setNoticeState(message || null);
    setTick((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNoticeState(null), timeout);
    return () => clearTimeout(timer);
  }, [notice, tick, timeout]);

  return [notice, setNotice];
}
