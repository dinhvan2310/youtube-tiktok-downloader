import { useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowDown,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleStop,
  Clipboard,
  Info,
  LoaderCircle,
  Search,
  X,
  XCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import { getJobs, stopJob } from './api'
import { SelectField } from './SelectField'

const label = kind => kind === 'crawl-batched' ? 'Source sync' : kind === 'metadata' ? 'Metadata enrichment' : 'Download'
const running = status => ['queued', 'running', 'stopping'].includes(status)
const statusLabel = status => status === 'done' ? 'Completed' : status === 'queued' ? 'Queued' : status === 'running' ? 'Running' : status === 'stopping' ? 'Stopping' : status === 'stopped' ? 'Stopped' : 'Failed'
const eventLevel = event => event?.level === 'error' ? 'error' : event?.level === 'warning' || event?.level === 'warn' ? 'warning' : 'info'
const eventMessage = event => event?.message || event?.event || 'Activity updated'
const shortTime = value => value ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'
const dateTime = value => value ? new Date(value).toLocaleString() : 'Time unavailable'

function StatusIcon({ status, size = 17 }) {
  if (running(status)) return <LoaderCircle className="spin" size={size} aria-hidden="true" />
  if (status === 'done') return <CheckCircle2 size={size} aria-hidden="true" />
  return <XCircle size={size} aria-hidden="true" />
}

function LogIcon({ level }) {
  if (level === 'error' || level === 'warning') return <AlertTriangle size={14} aria-hidden="true" />
  return <Info size={14} aria-hidden="true" />
}

export function ActivityDrawer({ open, onOpenChange }) {
  const queryClient = useQueryClient()
  const logRef = useRef(null)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [logFilter, setLogFilter] = useState('all')
  const [followLatest, setFollowLatest] = useState(true)
  const [showVideoProgress, setShowVideoProgress] = useState(false)
  const { data: jobs = [] } = useQuery({ queryKey: ['jobs'], queryFn: getJobs, enabled: open, refetchInterval: open ? 2000 : false })
  const stop = useMutation({
    mutationFn: stopJob,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
    onError: error => toast.error(error.message),
  })

  const rows = useMemo(() => jobs.filter(job => (
    (filter === 'all' || filter === 'running' && running(job.status) || job.status === filter)
    && JSON.stringify(job).toLowerCase().includes(search.toLowerCase())
  )), [jobs, filter, search])
  const activeCount = jobs.filter(job => running(job.status)).length
  const failedCount = jobs.filter(job => job.status === 'failed').length

  useEffect(() => {
    if (!rows.length) {
      setSelectedId('')
      return
    }
    if (!rows.some(job => job.id === selectedId)) {
      const priorityTask = rows.find(job => running(job.status)) || rows.find(job => job.status === 'failed') || rows[0]
      setSelectedId(priorityTask.id)
    }
  }, [rows, selectedId])

  useEffect(() => {
    setFollowLatest(true)
    setShowVideoProgress(false)
  }, [selectedId])

  const selected = rows.find(job => job.id === selectedId) || null
  const events = selected?.events || []
  const visibleEvents = useMemo(() => events.filter(event => logFilter === 'all' || eventLevel(event) === logFilter), [events, logFilter])
  const errorCount = events.filter(event => eventLevel(event) === 'error').length
  const warningCount = events.filter(event => eventLevel(event) === 'warning').length
  const genericProgress = useMemo(() => [...events].reverse().map(event => event.data || {}).find(data => Number.isFinite(Number(data.completed)) && Number(data.total) > 0) || null, [events])
  const downloadProgress = useMemo(() => {
    const planned = events.filter(event => event.event === 'download_source_plan').reduce((sum, event) => sum + Number(event.data?.planned || 0), 0)
    if (!planned) return null
    const completed = events.filter(event => event.event === 'download_completed').length
    const failed = events.filter(event => event.event === 'download_failed').length
    const active = [...events].reverse().find(event => ['download_downloading', 'download_finishing', 'download_starting'].includes(event.event))?.data || null
    const currentFraction = active?.stage === 'downloading' && Number.isFinite(Number(active.percent)) ? Number(active.percent) / 100 : active?.stage === 'finishing' ? 1 : 0
    return { planned, completed, failed, active, value: Math.min(100, Math.round((completed + failed + currentFraction) * 100 / planned)) }
  }, [events])
  const progress = downloadProgress ? { completed: downloadProgress.completed + downloadProgress.failed, total: downloadProgress.planned } : genericProgress
  const progressValue = downloadProgress?.value ?? (progress ? Math.min(100, Math.round(Number(progress.completed) / Number(progress.total) * 100)) : null)
  const videoProgress = useMemo(() => {
    const map = new Map()
    events.forEach(event => {
      if (!event.event?.startsWith('download_')) return
      const data = event.data || {}
      if (!data.video_id) return
      const current = map.get(data.video_id) || { id: data.video_id, title: data.title || data.video_id, percent: 0, stage: 'starting' }
      current.title = data.title || current.title
      current.stage = data.stage || current.stage
      if (event.event === 'download_downloading' && Number.isFinite(Number(data.percent))) current.percent = Number(data.percent)
      if (event.event === 'download_finishing') { current.stage = 'finishing'; current.percent = 100 }
      if (event.event === 'download_completed') { current.stage = 'completed'; current.percent = 100 }
      if (event.event === 'download_failed') { current.stage = 'failed'; current.error = data.error }
      map.set(data.video_id, current)
    })
    const priority = { failed: 0, downloading: 1, finishing: 1, starting: 2, completed: 3 }
    return [...map.values()].sort((a, b) => (priority[a.stage] ?? 2) - (priority[b.stage] ?? 2))
  }, [events])
  const resultMetrics = useMemo(() => Object.entries(selected?.result || {}).filter(([, value]) => typeof value === 'number' && Number.isFinite(value)).slice(0, 3), [selected])

  useEffect(() => {
    const node = logRef.current
    if (node && followLatest) node.scrollTop = node.scrollHeight
  }, [selectedId, visibleEvents.length, followLatest])

  const onLogScroll = event => {
    const node = event.currentTarget
    setFollowLatest(node.scrollHeight - node.scrollTop - node.clientHeight < 24)
  }

  const jumpToLatest = () => {
    const node = logRef.current
    if (node) node.scrollTop = node.scrollHeight
    setFollowLatest(true)
  }

  const copy = async () => {
    if (!selected) return
    await navigator.clipboard.writeText(events.map(event => `${event.created_at} ${eventLevel(event).toUpperCase()} ${eventMessage(event)}`).join('\n'))
    toast.success('Full task log copied')
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="activity-overlay" />
        <Dialog.Content className="activity-drawer">
          <header className="activity-header">
            <div>
              <Dialog.Title>Activity center</Dialog.Title>
              <Dialog.Description>Monitor background work and investigate failures.</Dialog.Description>
            </div>
            <Dialog.Close className="icon-button" aria-label="Close activity"><X size={18} /></Dialog.Close>
          </header>

          <aside className="activity-sidebar" aria-label="Task browser">
            <div className="activity-summary" role="status" aria-live="polite" aria-atomic="true">
              <span><b>{activeCount}</b> active</span>
              <span className={failedCount ? 'has-attention' : ''}><b>{failedCount}</b> attention</span>
            </div>
            <div className="activity-tools">
              <label className="search">
                <Search size={16} aria-hidden="true" />
                <input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search tasks" aria-label="Search tasks" />
              </label>
              <SelectField value={filter} onValueChange={setFilter} ariaLabel="Filter tasks" options={[
                { value: 'all', label: 'All tasks' },
                { value: 'running', label: 'Queued / Running' },
                { value: 'failed', label: 'Failed' },
                { value: 'done', label: 'Completed' },
              ]} />
            </div>
            <div className="task-list-heading"><span>Tasks</span><small>{rows.length} of {jobs.length}</small></div>
            <div className="task-list-scroll" role="list" aria-label="Recent tasks">
              {rows.map(job => (
                <button className={`job-summary task-row ${selected?.id === job.id ? 'selected' : ''}`} key={job.id} onClick={() => setSelectedId(job.id)} aria-pressed={selected?.id === job.id}>
                  <span className={`task-status-icon ${job.status}`}><StatusIcon status={job.status} /></span>
                  <span>
                    <strong>{label(job.kind)}</strong>
                    <small title={dateTime(job.started_at)}>{shortTime(job.started_at)} · {job.source_ids?.length || 0} sources</small>
                  </span>
                  <b className={`task-status ${job.status}`}>{statusLabel(job.status)}</b>
                </button>
              ))}
              {rows.length === 0 && <div className="table-state">No matching tasks.</div>}
            </div>
          </aside>

          {selected ? (
            <section className="task-detail-pane" aria-label="Selected task details">
              <div className="task-detail-heading">
                <div>
                  <span className="detail-title"><StatusIcon status={selected.status} size={16} /> <strong>{label(selected.kind)}</strong></span>
                  <small>{statusLabel(selected.status)} · Started {dateTime(selected.started_at)}</small>
                </div>
                <div className="job-actions">
                  <button onClick={copy}><Clipboard size={14} aria-hidden="true" /> Copy log</button>
                  {running(selected.status) && <button className="danger" onClick={() => stop.mutate(selected.id)} disabled={stop.isPending}><CircleStop size={14} aria-hidden="true" /> Stop</button>}
                </div>
              </div>

              <div className="task-overview-strip">
                <div className="task-metrics">
                  <span><b>{selected.source_ids?.length || 0}</b> sources</span>
                  {resultMetrics.map(([name, value]) => <span key={name}><b>{value}</b> {name.replaceAll('_', ' ')}</span>)}
                  <span className={errorCount ? 'attention' : ''}><b>{errorCount}</b> errors</span>
                </div>
                {progressValue !== null && (
                  <div className="task-progress" aria-label={`${progressValue}% complete`}>
                    <div><span>{progressValue}% complete</span><b>{progress.completed} / {progress.total}</b></div>
                    <i><em style={{ width: `${progressValue}%` }} /></i>
                  </div>
                )}
                {videoProgress.length > 0 && (
                  <button className="video-progress-toggle" onClick={() => setShowVideoProgress(value => !value)} aria-expanded={showVideoProgress}>
                    <span>{videoProgress.length} video details</span>
                    {showVideoProgress ? <ChevronUp size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}
                  </button>
                )}
              </div>

              {showVideoProgress && videoProgress.length > 0 && (
                <div className="download-video-progress" aria-label="Per-video download progress">
                  {videoProgress.map(video => (
                    <div className={`download-video-row ${video.stage}`} key={video.id}>
                      <div className="download-video-meta">
                        <strong title={video.title}>{video.title}</strong>
                        <span>{video.stage === 'completed' ? 'Completed' : video.stage === 'failed' ? 'Failed' : video.stage === 'finishing' ? 'Finalizing' : video.percent > 0 ? `${Math.round(video.percent)}%` : 'Preparing'}</span>
                      </div>
                      <i><em style={{ width: `${Math.max(0, Math.min(100, video.percent))}%` }} /></i>
                    </div>
                  ))}
                </div>
              )}

              <div className="tasklog-toolbar">
                <div className="log-filter-group" aria-label="Filter log messages">
                  {[
                    ['all', 'All', events.length],
                    ['error', 'Errors', errorCount],
                    ['warning', 'Warnings', warningCount],
                  ].map(([value, name, count]) => <button key={value} className={logFilter === value ? 'active' : ''} onClick={() => setLogFilter(value)} aria-pressed={logFilter === value}>{name} <b>{count}</b></button>)}
                </div>
                <button className={`follow-log ${followLatest ? 'following' : ''}`} onClick={jumpToLatest} aria-label="Jump to latest log message">
                  <ArrowDown size={14} aria-hidden="true" /> {followLatest ? 'Live' : 'Latest'}
                </button>
              </div>
              <div className="tasklog-scroll" ref={logRef} onScroll={onLogScroll} role="log" aria-live="polite" aria-relevant="additions text">
                {visibleEvents.length ? visibleEvents.map((event, index) => {
                  const level = eventLevel(event)
                  return (
                    <article className={`log-event ${level}`} key={event.id || `${event.created_at}-${event.event}-${index}`}>
                      <time title={dateTime(event.created_at)}>{shortTime(event.created_at)}</time>
                      <span className="log-level"><LogIcon level={level} /><b>{level === 'error' ? 'Error' : level === 'warning' ? 'Warn' : 'Info'}</b></span>
                      <p>{eventMessage(event)}</p>
                    </article>
                  )
                }) : <div className="log-empty">No {logFilter === 'all' ? '' : `${logFilter} `}messages for this task.</div>}
              </div>
            </section>
          ) : (
            <section className="task-detail-empty">
              <Info size={22} aria-hidden="true" />
              <strong>No task selected</strong>
              <span>Adjust the filters or wait for a new background task.</span>
            </section>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
