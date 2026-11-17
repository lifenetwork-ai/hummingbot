import { useEffect, useState } from 'react'

/** Re-evaluate cached observation age without issuing additional network reads. */
export function useObservationTime(enabled: boolean) {
  const [, tick] = useState(0)
  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => tick((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [enabled])
  return Date.now()
}
