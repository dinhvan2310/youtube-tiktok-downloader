import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { eventUrl } from './api'

export function useJobStream(jobId, onFinished) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!jobId) return undefined
    const events = new EventSource(eventUrl(jobId))
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: ['overview'] })
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    }
    events.addEventListener('batch_ready', refresh)
    events.addEventListener('completed', () => { refresh(); onFinished?.('done'); events.close() })
    events.addEventListener('stopped', () => { refresh(); onFinished?.('stopped'); events.close() })
    events.addEventListener('failed', () => { refresh(); onFinished?.('failed'); events.close() })
    return () => events.close()
  }, [jobId, onFinished, queryClient])
}
