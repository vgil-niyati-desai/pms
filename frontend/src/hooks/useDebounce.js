import { useEffect, useState } from "react";

/** `value`, but only after it has stopped changing for `delay` ms. */
export default function useDebounce(value, delay = 300) {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return settled;
}
