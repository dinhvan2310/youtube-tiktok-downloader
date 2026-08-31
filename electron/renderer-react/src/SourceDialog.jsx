import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, LoaderCircle, X } from 'lucide-react'
import { toast } from 'sonner'
import { createSource, updateSource, validateSource } from './api'

const schema = z.object({
  path_download: z.string().min(1, 'Chọn thư mục tải xuống.'),
  note: z.string().trim(),
  links: z.string().min(1, 'Nhập ít nhất một channel link.'),
})

export function SourceDialog({ source, open, onOpenChange, onSync }) {
  const queryClient = useQueryClient()
  const [duplicates, setDuplicates] = useState([])
  const form = useForm({
    resolver: zodResolver(schema),
    values: { path_download: source?.path_download || '', note: source?.note || '', links: source?.links?.join('\n') || '' },
  })
  const save = useMutation({
    mutationFn: async ({ values, move = false }) => {
      const payload = { source_id: source?.id || '', path_download: values.path_download.trim(), note: values.note.trim(), links: values.links.split(/\r?\n/).map(item => item.trim()).filter(Boolean), reup_source: 'youtube_tiktok', move_duplicate_links: move }
      const validation = await validateSource(payload)
      if (validation.errors?.length) throw new Error(validation.errors.join('\n'))
      if (validation.duplicates?.length && !move) { setDuplicates(validation.duplicates); return null }
      return source ? updateSource(source.id, payload) : createSource(payload)
    },
    onSuccess: result => {
      if (!result) return
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      queryClient.invalidateQueries({ queryKey: ['overview'] })
      toast.success(source ? 'Đã cập nhật source' : 'Đã thêm source')
      onOpenChange(false)
      onSync?.(result.id)
    },
    onError: error => toast.error(error.message),
  })
  const browse = async () => { const path = await window.desktop?.chooseDirectory(); if (path) form.setValue('path_download', path, { shouldValidate: true }) }
  const submit = values => { setDuplicates([]); save.mutate({ values }) }
  const move = () => save.mutate({ values: form.getValues(), move: true })
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content"><div className="dialog-heading"><div><p className="eyebrow">SOURCE</p><Dialog.Title>{source ? 'Edit source' : 'Add source'}</Dialog.Title><Dialog.Description>Validate channel ownership before saving and syncing.</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="Đóng"><X size={18} /></Dialog.Close></div><form onSubmit={form.handleSubmit(submit)} className="source-form"><label>Download folder<div className="input-action"><input {...form.register('path_download')} placeholder="Chọn thư mục" /><button type="button" onClick={browse}>Browse</button></div>{form.formState.errors.path_download && <small role="alert">{form.formState.errors.path_download.message}</small>}</label><label>Team note<input {...form.register('note')} placeholder="Ví dụ: Fitness creators" /></label><label>Channel links <span>One URL per line</span><textarea {...form.register('links')} rows="7" placeholder="https://youtube.com/@channel/videos" />{form.formState.errors.links && <small role="alert">{form.formState.errors.links.message}</small>}</label>{duplicates.length > 0 && <div className="conflict-panel" role="alert"><AlertTriangle size={17} /><div><strong>Channel đã thuộc source khác</strong>{duplicates.map(item => <p key={item.link}>{item.source_label}: {item.link}</p>)}<button type="button" onClick={move}>Move channel here</button></div></div>}<div className="dialog-actions"><Dialog.Close className="button button-ghost" type="button">Cancel</Dialog.Close><button disabled={save.isPending} className="button button-primary">{save.isPending && <LoaderCircle className="spin" size={16} />}{source ? 'Save changes' : 'Save & sync'}</button></div></form></Dialog.Content></Dialog.Portal></Dialog.Root>
}
