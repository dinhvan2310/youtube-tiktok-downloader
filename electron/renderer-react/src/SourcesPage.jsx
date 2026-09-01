import { useEffect, useMemo, useRef, useState } from 'react'
import * as Checkbox from '@radix-ui/react-checkbox'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Check, FolderOpen, MoreHorizontal, Pencil, Plus, RefreshCw, Search, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { createJob, deleteSource, getSources, setSourceArchived } from './api'
import { DeleteSourceDialog } from './DeleteSourceDialog'
import { ImportSourcesDialog } from './ImportSourcesDialog'
import { SourceDialog } from './SourceDialog'
import { SelectField } from './SelectField'

const sourceName = source => source?.label || source?.note || source?.path_download?.split(/[\\/]/).pop() || 'Source'
const relative = value => {
  if (!value) return 'Chưa sync'
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 60000))
  if (minutes < 1) return 'Vừa xong'
  if (minutes < 60) return `${minutes}m trước`
  if (minutes < 1440) return `${Math.round(minutes / 60)}h trước`
  return `${Math.round(minutes / 1440)}d trước`
}

export function SourcesPage({ startJob }) {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('active')
  const [dialog, setDialog] = useState({ open: false, source: null })
  const [deleteDialog, setDeleteDialog] = useState({ open: false, source: null })
  const [importOpen, setImportOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState(() => new Set())
  const [bulkPending, setBulkPending] = useState(false)
  const selectAllRef = useRef(null)
  const lastSelectedIdRef = useRef(null)
  const selectionModifierRef = useRef({ shiftKey: false })
  const { data: sources = [], isPending, error } = useQuery({ queryKey: ['sources', true], queryFn: () => getSources(true) })
  const rows = useMemo(() => sources.filter(source => {
    const matches = JSON.stringify(source).toLowerCase().includes(query.toLowerCase())
    if (!matches) return false
    if (filter === 'archived') return source.status === 'archived'
    if (filter === 'review') return source.status !== 'archived' && source.review_count > 0
    if (filter === 'sync') return source.status !== 'archived' && source.stale
    return source.status !== 'archived'
  }), [sources, query, filter])
  const visibleIds = useMemo(() => rows.map(source => String(source.id)), [rows])
  const selectedVisibleCount = visibleIds.filter(id => selectedIds.has(id)).length
  const allVisibleSelected = visibleIds.length > 0 && selectedVisibleCount === visibleIds.length
  const someVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someVisibleSelected
  }, [someVisibleSelected])

  useEffect(() => {
    const knownIds = new Set(sources.map(source => String(source.id)))
    setSelectedIds(current => new Set([...current].filter(id => knownIds.has(id))))
  }, [sources])

  const toggleSelected = (id, { shiftKey = false } = {}) => {
    const key = String(id)
    const anchorId = lastSelectedIdRef.current
    const rangeStart = shiftKey && anchorId ? visibleIds.indexOf(anchorId) : -1
    const rangeEnd = visibleIds.indexOf(key)
    lastSelectedIdRef.current = key
    setSelectedIds(current => {
      const next = new Set(current)
      if (rangeStart >= 0 && rangeEnd >= 0) {
        const [from, to] = [rangeStart, rangeEnd].sort((a, b) => a - b)
        visibleIds.slice(from, to + 1).forEach(visibleId => next.add(visibleId))
      } else if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }
  const toggleAllVisible = () => setSelectedIds(current => {
    const next = new Set(current)
    if (allVisibleSelected) visibleIds.forEach(id => next.delete(id))
    else visibleIds.forEach(id => next.add(id))
    return next
  })
  const clearSelection = () => {
    lastSelectedIdRef.current = null
    setSelectedIds(new Set())
  }
  const recordSelectionModifier = event => {
    selectionModifierRef.current = { shiftKey: Boolean(event.shiftKey) }
  }
  const removeReviewItemsForSources = ids => {
    const removedIds = new Set(ids.map(String))
    queryClient.setQueriesData({ queryKey: ['videos'] }, current => {
      if (!current?.items) return current
      return { ...current, items: current.items.filter(item => !removedIds.has(String(item.source_id))) }
    })
  }
  const archiveSelected = async () => {
    const targets = sources.filter(source => selectedIds.has(String(source.id)) && source.status !== 'archived')
    if (!targets.length) return
    if (!confirm(`Archive ${targets.length} selected source${targets.length === 1 ? '' : 's'}? Folder and history stay intact.`)) return
    setBulkPending(true)
    try {
      for (const source of targets) await setSourceArchived(source.id, true)
      clearSelection()
      await queryClient.invalidateQueries()
      toast.success(`${targets.length} source${targets.length === 1 ? '' : 's'} archived`)
    } catch (bulkError) {
      toast.error(bulkError.message)
    } finally {
      setBulkPending(false)
    }
  }
  const deleteSelected = async () => {
    const targets = sources.filter(source => selectedIds.has(String(source.id)) && source.status === 'archived')
    if (!targets.length) return
    if (!confirm(`Delete ${targets.length} archived source${targets.length === 1 ? '' : 's'} permanently? Downloaded files stay on disk.`)) return
    setBulkPending(true)
    try {
      for (const source of targets) await deleteSource(source.id)
      removeReviewItemsForSources(targets.map(source => source.id))
      clearSelection()
      await queryClient.invalidateQueries()
      toast.success(`${targets.length} archived source${targets.length === 1 ? '' : 's'} deleted`)
    } catch (bulkError) {
      toast.error(bulkError.message)
    } finally {
      setBulkPending(false)
    }
  }
  const lifecycle = useMutation({
    mutationFn: ({ id, archived }) => setSourceArchived(id, archived),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries()
      toast.success(variables.archived ? 'Source archived' : 'Source restored')
    },
    onError: mutationError => toast.error(mutationError.message),
  })
  const syncSource = async id => {
    try {
      const job = await createJob({ kind: 'crawl-batched', source_ids: [id], batch_size: 50, resume: false })
      startJob(job.id)
      toast.success('Source sync queued')
    } catch (syncError) {
      toast.error(syncError.message)
    }
  }

  return <section className="page sources-page">
    <header className="page-header"><div><p className="eyebrow">1 / SOURCES</p><h1>Sources</h1><p>Channel ownership, health and background sync in one place.</p></div><button className="button button-primary" onClick={() => setDialog({ open: true, source: null })}><Plus size={17} /> Add source</button></header>
    <div className="toolbar"><label className="search"><Search size={17} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search sources, paths or links" /></label><SelectField value={filter} onValueChange={setFilter} ariaLabel="Filter sources" options={[{ value: 'active', label: 'Active' }, { value: 'review', label: 'Needs review' }, { value: 'sync', label: 'Needs sync' }, { value: 'archived', label: 'Archived' }]} /><div className="toolbar-spacer" /><button className="button button-ghost" onClick={() => setImportOpen(true)}><Upload size={16} /> Import</button><button className="icon-button" aria-label="Refresh sources" onClick={() => queryClient.invalidateQueries()}><RefreshCw size={17} /></button></div>
    {selectedIds.size > 0 && <div className="selection-bar" role="status" aria-live="polite"><strong>{selectedIds.size} selected</strong><span>{selectedVisibleCount} in this view · Shift-click range · Ctrl/Cmd-click toggle</span>{targetsForArchive(sources, selectedIds).length > 0 && <button className="bulk-action primary" disabled={bulkPending} onClick={archiveSelected}>{bulkPending ? 'Archiving…' : 'Archive selected'}</button>}{targetsForDelete(sources, selectedIds).length > 0 && <button className="bulk-action danger" disabled={bulkPending} onClick={deleteSelected}>{bulkPending ? 'Deleting…' : 'Delete selected'}</button>}<button onClick={clearSelection}>Clear selection</button></div>}
    <div className="table-card sources-table"><table><thead><tr><th className="source-select-cell"><input ref={selectAllRef} className="source-checkbox" type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} disabled={!visibleIds.length || isPending} aria-label="Select all visible sources" /></th><th>Source</th><th>Path</th><th>Links</th><th>Review</th><th>Queued</th><th>Capacity</th><th>Last sync</th><th>Actions</th></tr></thead><tbody>
      {isPending && <tr><td colSpan="9"><div className="table-state">Loading sources…</div></td></tr>}
      {error && <tr><td colSpan="9"><div className="table-state error">{error.message}</div></td></tr>}
      {!isPending && rows.map(source => <tr key={source.id} className={selectedIds.has(String(source.id)) ? 'selected' : undefined}>
        <td className="source-select-cell"><Checkbox.Root className="source-checkbox" checked={selectedIds.has(String(source.id))} onPointerDown={recordSelectionModifier} onClick={recordSelectionModifier} onKeyDown={recordSelectionModifier} onCheckedChange={() => toggleSelected(source.id, selectionModifierRef.current)} aria-label={`Select ${sourceName(source)}`}><Checkbox.Indicator><Check size={13} /></Checkbox.Indicator></Checkbox.Root></td>
        <td><div className="source"><div><strong>{sourceName(source)}</strong><small>{source.status === 'archived' ? 'Archived' : source.stale ? 'Needs sync' : 'Active'}</small></div></div></td>
        <td className="source-path-cell"><button className="source-path-button" title={source.path_download} onClick={() => window.desktop?.openPath(source.path_download)}><FolderOpen size={14} aria-hidden="true" /><span>{source.path_download}</span></button></td>
        <td className="number">{source.links?.length || 0}</td><td className="number">{source.review_count || 0}</td><td className="number">{source.queued_count || 0}</td>
        <td><strong>{source.folder_count || 0}/{(source.folder_count || 0) + (source.capacity || 0)}</strong><small>{source.capacity || 0} slots</small></td><td>{relative(source.last_crawl_at)}</td>
        <td><div className="row-actions">{source.status === 'archived' ? <><button className="row-action" onClick={() => lifecycle.mutate({ id: source.id, archived: false })}><ArchiveRestore size={15} /> Restore</button><button className="icon-button danger" aria-label={`Delete ${sourceName(source)} permanently`} onClick={() => setDeleteDialog({ open: true, source })}><Trash2 size={16} /></button></> : <><button className="row-action" onClick={() => syncSource(source.id)}><RefreshCw size={15} /> Sync</button><button className="icon-button" aria-label={`Edit ${sourceName(source)}`} onClick={() => setDialog({ open: true, source })}><Pencil size={16} /></button><button className="icon-button danger" aria-label={`Archive ${sourceName(source)}`} onClick={() => { if (confirm(`Archive “${sourceName(source)}”? Folder and history stay intact.`)) lifecycle.mutate({ id: source.id, archived: true }) }}><Archive size={16} /></button></>}</div></td>
      </tr>)}
      {!isPending && rows.length === 0 && <tr><td colSpan="9"><div className="table-state"><MoreHorizontal size={18} /> No sources match this view.</div></td></tr>}
    </tbody></table></div>
    <SourceDialog source={dialog.source} open={dialog.open} onOpenChange={open => setDialog(current => ({ ...current, open }))} onSync={syncSource} />
    <ImportSourcesDialog open={importOpen} onOpenChange={setImportOpen} />
    <DeleteSourceDialog source={deleteDialog.source} open={deleteDialog.open} onDeleted={sourceId => removeReviewItemsForSources([sourceId])} onOpenChange={open => setDeleteDialog(current => ({ ...current, open }))} />
  </section>
}

const targetsForArchive = (sources, selectedIds) => sources.filter(source => selectedIds.has(String(source.id)) && source.status !== 'archived')
const targetsForDelete = (sources, selectedIds) => sources.filter(source => selectedIds.has(String(source.id)) && source.status === 'archived')
