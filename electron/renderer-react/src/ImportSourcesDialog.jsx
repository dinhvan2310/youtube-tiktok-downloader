import { useEffect, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Check, FileSpreadsheet, FolderRoot, LoaderCircle, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, getConfig } from './api'

const fileName = path => path?.split(/[\\/]/).pop() || ''

export function ImportSourcesDialog({ open, onOpenChange }) {
  const queryClient = useQueryClient()
  const errorRef = useRef(null)
  const [filePath, setFilePath] = useState('')
  const [rootPath, setRootPath] = useState('')
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: getConfig, enabled: open })

  useEffect(() => {
    if (open && !rootPath && config?.source_root_path) setRootPath(config.source_root_path)
  }, [open, rootPath, config])

  const resetPreview = () => { setPreview(null); setError('') }
  const close = next => {
    if (busy) return
    onOpenChange(next)
    if (!next) {
      setFilePath('')
      setRootPath('')
      setPreview(null)
      setError('')
    }
  }
  const chooseFile = async () => {
    const path = await window.desktop?.chooseFile([{ name: 'Excel source list', extensions: ['xlsx', 'xls'] }])
    if (path) { setFilePath(path); resetPreview() }
  }
  const chooseRoot = async () => {
    const path = await window.desktop?.chooseDirectory()
    if (path) { setRootPath(path); resetPreview() }
  }
  const showError = message => {
    setError(message)
    requestAnimationFrame(() => errorRef.current?.focus())
  }
  const review = async () => {
    if (!filePath) return showError('Choose an Excel file to import.')
    if (!rootPath) return showError('Choose the download root folder for these sources.')
    setBusy(true)
    setError('')
    try {
      const result = await api('/excel/preview', { method: 'POST', body: JSON.stringify({ path: filePath, root_path: rootPath }) })
      setPreview(result)
      if (result.errors?.length) showError(`${result.errors.length} row(s) need attention before import.`)
    } catch (requestError) {
      showError(requestError.message)
    } finally {
      setBusy(false)
    }
  }
  const importSources = async () => {
    if (!preview || preview.errors?.length || preview.rows < 1) return
    setBusy(true)
    setError('')
    try {
      await api('/excel/import', { method: 'POST', body: JSON.stringify({ path: filePath, root_path: rootPath }) })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sources'] }),
        queryClient.invalidateQueries({ queryKey: ['config'] }),
        queryClient.invalidateQueries({ queryKey: ['overview'] }),
      ])
      toast.success(`${preview.rows} source(s) imported`)
      setBusy(false)
      close(false)
    } catch (requestError) {
      showError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const hasBlockingErrors = Boolean(preview?.errors?.length)
  return <Dialog.Root open={open} onOpenChange={close}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content import-dialog" aria-describedby="import-description">
    <div className="dialog-heading"><div><p className="eyebrow">BULK SETUP</p><Dialog.Title>Import sources</Dialog.Title><Dialog.Description id="import-description">Review destinations and validation results before anything is saved.</Dialog.Description></div><Dialog.Close className="icon-button" disabled={busy} aria-label="Close import"><X size={18} /></Dialog.Close></div>
    <ol className="import-steps" aria-label="Import progress"><li className={!preview ? 'active' : 'complete'}><span>{preview ? <Check size={13} /> : '1'}</span><div><strong>Choose files</strong><small>Excel and destination</small></div></li><li className={preview ? 'active' : ''}><span>2</span><div><strong>Review</strong><small>Validate before saving</small></div></li></ol>
    {error && <div className="import-error" role="alert" tabIndex="-1" ref={errorRef}><AlertTriangle size={17} aria-hidden="true" /><div><strong>Import needs attention</strong><p>{error}</p></div></div>}
    {!preview ? <div className="import-config">
      <section><span className="import-field-icon"><FileSpreadsheet size={19} aria-hidden="true" /></span><div><strong>Source spreadsheet</strong><small>Required: folder name and at least one link.</small><p title={filePath}>{filePath ? fileName(filePath) : 'No Excel file selected'}</p></div><button type="button" className="button button-ghost" onClick={chooseFile}>{filePath ? 'Change' : 'Choose file'}</button></section>
      <section><span className="import-field-icon"><FolderRoot size={19} aria-hidden="true" /></span><div><strong>Download root</strong><small>Folder names in Excel become child folders here.</small><p title={rootPath}>{rootPath || 'No root folder selected'}</p></div><button type="button" className="button button-ghost" onClick={chooseRoot}>{rootPath ? 'Change' : 'Choose folder'}</button></section>
    </div> : <div className="import-review">
      <div className="import-summary"><span><b>{preview.rows}</b><small>Valid sources</small></span><span className={hasBlockingErrors ? 'danger' : ''}><b>{preview.errors?.length || 0}</b><small>Blocking errors</small></span><span className={preview.duplicates?.length ? 'warning' : ''}><b>{preview.duplicates?.length || 0}</b><small>Existing links</small></span></div>
      <div className="import-destination"><FolderRoot size={16} aria-hidden="true" /><span><small>Destination root</small><strong title={preview.root_path}>{preview.root_path}</strong></span></div>
      {preview.errors?.length > 0 && <div className="import-error-list" aria-label="Rows with errors">{preview.errors.map((item, index) => <p key={`${item}-${index}`}>{item}</p>)}</div>}
      {preview.duplicates?.length > 0 && <div className="import-warning"><AlertTriangle size={16} aria-hidden="true" /><span><strong>{preview.duplicates.length} link(s) already belong to another source.</strong><small>Review ownership after import to avoid duplicate crawling.</small></span></div>}
      {!hasBlockingErrors && <div className="import-preview-list" aria-label="Source preview">{preview.preview.slice(0, 8).map(item => <div key={item.folder}><span>{fileName(item.folder).slice(0, 2).toUpperCase()}</span><strong>{fileName(item.folder)}</strong><small>{item.links} link{item.links === 1 ? '' : 's'}</small></div>)}{preview.rows > 8 && <p>+{preview.rows - 8} more sources</p>}</div>}
    </div>}
    <div className="dialog-actions import-actions">{preview ? <button type="button" className="button button-ghost" disabled={busy} onClick={() => { setPreview(null); setError('') }}><ArrowLeft size={15} /> Back</button> : <Dialog.Close className="button button-ghost" disabled={busy}>Cancel</Dialog.Close>}{!preview ? <button type="button" className="button button-primary" disabled={busy || !filePath || !rootPath} onClick={review}>{busy && <LoaderCircle className="spin" size={16} />}Review import</button> : <button type="button" className="button button-primary" disabled={busy || hasBlockingErrors || preview.rows < 1} onClick={importSources}>{busy && <LoaderCircle className="spin" size={16} />}Import {preview.rows} sources</button>}</div>
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
