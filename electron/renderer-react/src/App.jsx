import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Activity, Check, Download, FolderOpen, RefreshCw, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { ensureSync, getJobs, getOverview } from './api'
import { ActivityDrawer } from './ActivityDrawer'
import reupStudioLogo from './assets/reup-studio-logo.png'

const SourcesPage = lazy(() => import('./SourcesPage').then(module => ({ default: module.SourcesPage })))
const ReviewPage = lazy(() => import('./ReviewPage').then(module => ({ default: module.ReviewPage })))
const QueuePage = lazy(() => import('./QueuePage').then(module => ({ default: module.QueuePage })))
const SettingsPage = lazy(() => import('./SettingsPage').then(module => ({ default: module.SettingsPage })))
const nav = [{ id: 'sources', label: 'Sources', icon: FolderOpen }, { id: 'review', label: 'Review', icon: Check }, { id: 'queue', label: 'Queue', icon: Download }, { id: 'settings', label: 'Settings', icon: Settings }]
const taskLabel = kind => kind === 'crawl-batched' ? 'Source sync' : kind === 'metadata' ? 'Metadata enrichment' : 'Download'
const taskPercent = job => {
  if (!job) return null
  const event = [...(job.events || []).reverse()].find(item => ['download_summary', 'download_source_plan'].includes(item.event) && Number(item.data?.total) > 0)
  if (!event) return null
  return Math.min(100, Math.round((Number(event.data.completed || 0) + Number(event.data.failed || 0)) * 100 / Number(event.data.total)))
}

export default function App() {
  const queryClient = useQueryClient(); const reducedMotion = useReducedMotion(); const synced = useRef(false); const seenJobs = useRef(new Map())
  const [view, setView] = useState('sources'); const [activityOpen, setActivityOpen] = useState(false)
  const { data: overview } = useQuery({ queryKey: ['overview'], queryFn: getOverview, refetchInterval: 10_000 })
  const { data: jobs = [] } = useQuery({ queryKey: ['jobs'], queryFn: getJobs, refetchInterval: 2_000 })
  const running = jobs.filter(job => ['queued', 'running', 'stopping'].includes(job.status))
  const activeDownloadPercent = taskPercent(running.find(job => job.kind === 'download'))
  const sync = useMutation({ mutationFn: ensureSync, onSuccess: () => queryClient.invalidateQueries(), onError: error => toast.error(`Auto-sync: ${error.message}`) })
  useEffect(() => {
    if (!overview?.sources || synced.current) return
    synced.current = true; sync.mutate()
    const timer = window.setInterval(() => sync.mutate(), 15 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [overview?.sources])
  useEffect(() => {
    const previous = seenJobs.current
    jobs.forEach(job => {
      const before = previous.get(job.id)
      if (before && ['queued', 'running', 'stopping'].includes(before) && ['done', 'failed', 'stopped'].includes(job.status)) {
        const result = job.result || {}; const detail = job.status === 'done' ? `${result.crawled ?? result.downloaded ?? result.enriched ?? 0} completed` : job.status
      const title = `${taskLabel(job.kind)} ${job.status === 'done' ? 'completed' : job.status}`
      // Successful background maintenance is visible in Tasks without interrupting the user.
      // Downloads and failures still deserve both in-app and system notifications.
      const shouldNotify = job.status === 'failed' || (job.kind === 'download' && job.status === 'done')
      if (shouldNotify) {
        toast[job.status === 'failed' ? 'error' : 'success'](`${title} — ${detail}`)
        window.desktop?.notify?.({ title: 'Reup Studio', body: `${title}: ${detail}` })
      }
      // A completed download changes Review, Queue, Sources and overview counts.
      // Refresh all affected caches immediately instead of waiting for their polling window.
      queryClient.invalidateQueries({ queryKey: ['overview'] })
      queryClient.invalidateQueries({ queryKey: ['queue'] })
      queryClient.invalidateQueries({ queryKey: ['videos'] })
      queryClient.invalidateQueries({ queryKey: ['sources'] })
    }
      previous.set(job.id, job.status)
    })
  }, [jobs, queryClient])
  const Page = view === 'sources' ? SourcesPage : view === 'review' ? ReviewPage : view === 'queue' ? QueuePage : SettingsPage
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-mark"><img src={reupStudioLogo} alt="" /></span><div><strong>Reup Studio</strong><small>Internal workspace</small></div></div><nav aria-label="Main navigation">{nav.map(item => { const Icon = item.icon; const count = item.id === 'sources' ? overview?.sources : item.id === 'review' ? overview?.counts?.review : item.id === 'queue' ? overview?.counts?.queued : null; return <button key={item.id} className={`nav-item ${view === item.id ? 'active' : ''}`} onClick={() => setView(item.id)}><Icon size={18} aria-hidden="true" /><span>{item.label}</span>{count != null && <b>{count}</b>}</button> })}</nav><div className="sidebar-footer"><span className={running.length ? 'syncing' : ''} />{running.length ? `${running.length} task${running.length === 1 ? '' : 's'} active` : 'Auto-sync ready'}</div></aside><main><header className="topbar"><p>TEAM REUP / {view.toUpperCase()}</p><div className="topbar-actions"><button className="top-refresh" onClick={() => queryClient.invalidateQueries()}><RefreshCw size={16} /> Refresh</button><button className={`activity-button ${running.length ? 'running' : ''}`} onClick={() => setActivityOpen(true)}><Activity size={17} /><span>Tasks</span>{activeDownloadPercent != null && <b className="task-menu-progress" aria-label={`Download progress ${activeDownloadPercent}%`}>{activeDownloadPercent}%</b>}{running.length > 0 && <i />}</button></div></header><Suspense fallback={<div className="page"><div className="table-state">Loading workspace…</div></div>}><AnimatePresence mode="wait"><motion.div className={`page-frame page-frame-${view}`} key={view} initial={reducedMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={reducedMotion ? {} : { opacity: 0, y: -4 }} transition={{ duration: .16 }}><Page startJob={() => queryClient.invalidateQueries()} /></motion.div></AnimatePresence></Suspense></main><ActivityDrawer open={activityOpen} onOpenChange={setActivityOpen} /></div>
}
