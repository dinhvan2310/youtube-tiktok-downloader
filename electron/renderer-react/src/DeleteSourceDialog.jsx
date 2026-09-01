import { useEffect, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, FolderLock, LoaderCircle, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { deleteSource } from './api'

const label = source => source?.label || source?.note || source?.path_download?.split(/[\\/]/).pop() || 'Source'

export function DeleteSourceDialog({ source, open, onOpenChange }) {
  const queryClient = useQueryClient()
  const [confirmed, setConfirmed] = useState(false)
  useEffect(() => { if (!open) setConfirmed(false) }, [open])
  const remove = useMutation({
    mutationFn: () => deleteSource(source.id),
    onSuccess: result => {
      queryClient.invalidateQueries()
      toast.success(`Deleted ${label(source)} · downloaded files were kept`)
      onOpenChange(false)
      return result
    },
    onError: error => toast.error('Could not delete source', { description: error.message }),
  })
  if (!source) return null
  return <Dialog.Root open={open} onOpenChange={next => { if (!remove.isPending) onOpenChange(next) }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content delete-source-dialog">
    <div className="dialog-heading"><div><p className="eyebrow danger-eyebrow">PERMANENT ACTION</p><Dialog.Title>Delete source?</Dialog.Title><Dialog.Description>Remove this archived source and its app history.</Dialog.Description></div><Dialog.Close className="icon-button" disabled={remove.isPending} aria-label="Close delete confirmation"><X size={18} /></Dialog.Close></div>
    <div className="delete-source-identity"><span>{label(source).slice(0, 2).toUpperCase()}</span><div><strong>{label(source)}</strong><small title={source.path_download}>{source.path_download}</small></div></div>
    <div className="delete-impact"><AlertTriangle size={18} aria-hidden="true" /><div><strong>This removes from the app</strong><p>{source.review_count || 0} review items, {source.queued_count || 0} queued items and {source.downloaded_count || 0} download-history records.</p></div></div>
    <div className="delete-kept"><FolderLock size={18} aria-hidden="true" /><div><strong>Files stay on disk</strong><p>The download folder and existing video files will not be deleted.</p></div></div>
    <label className="delete-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>I understand the app history cannot be restored.</span></label>
    <div className="dialog-actions"><Dialog.Close className="button button-ghost" disabled={remove.isPending}>Cancel</Dialog.Close><button className="button button-danger" disabled={!confirmed || remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}Delete permanently</button></div>
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
