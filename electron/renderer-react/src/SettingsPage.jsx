import { useForm } from 'react-hook-form'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, LoaderCircle, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { getConfig, saveConfig } from './api'
import { SelectField } from './SelectField'

const numeric = ['quality_height', 'target_videos_per_page', 'source_thread_count', 'download_source_concurrency', 'global_download_concurrency', 'metadata_workers']

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: config, isPending } = useQuery({ queryKey: ['config'], queryFn: getConfig })
  const form = useForm({ values: config || {} })
  const save = useMutation({
    mutationFn: saveConfig,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['config'] }); toast.success('Settings saved') },
    onError: error => toast.error(error.message),
  })
  if (isPending) return <section className="page"><div className="table-state"><LoaderCircle className="spin" /> Loading settings…</div></section>
  const submit = values => save.mutate({ ...config, ...values, ...Object.fromEntries(numeric.map(key => [key, +values[key]])) })
  const chooseCookies = async field => {
    const path = await window.desktop?.chooseFile([{ name: 'Netscape cookies', extensions: ['txt'] }, { name: 'All files', extensions: ['*'] }])
    if (path) form.setValue(field, path, { shouldDirty: true })
  }
  return <section className="page">
    <header className="page-header"><div><p className="eyebrow">WORKSPACE</p><h1>Settings</h1><p>Downloads, task lanes and platform access.</p></div><button className="button button-primary" disabled={save.isPending} onClick={form.handleSubmit(submit)}>Save changes</button></header>
    <form className="settings-grid">
      <label>Maximum resolution<SelectField value={String(form.watch('quality_height') ?? 0)} onValueChange={value => form.setValue('quality_height', value, { shouldDirty: true })} options={[{ value: '0', label: 'No limit (best available)' }, { value: '720', label: '720p' }, { value: '1080', label: '1080p' }, { value: '1440', label: '1440p' }]} /></label>
      <label>Videos per source cap<input type="number" min="1" {...form.register('target_videos_per_page')} /></label>
      <label>Crawl sources at once<input type="number" min="1" max="8" {...form.register('source_thread_count')} /></label>
      <label>Download sources at once<input type="number" min="1" max="8" {...form.register('download_source_concurrency')} /></label>
      <label>Global downloads at once<input type="number" min="1" max="32" {...form.register('global_download_concurrency')} /></label>
      <label>Metadata workers<input type="number" min="1" max="16" {...form.register('metadata_workers')} /></label>
      <fieldset className="settings-full youtube-access platform-access"><legend><ShieldCheck size={16} aria-hidden="true" /> Platform access</legend><p>Cookie files are isolated by platform and take priority over browser access. Public content can still work without them.</p><div className="platform-access-grid"><section className="platform-access-row"><div className="platform-access-label"><strong>YouTube</strong><span>Verification and restricted videos</span></div><label>Cookies file<span className="file-input"><input placeholder="YouTube cookies.txt" {...form.register('youtube_cookies_file')} /><button type="button" className="icon-button" onClick={() => chooseCookies('youtube_cookies_file')} aria-label="Choose YouTube cookies.txt file"><FolderOpen size={16} /></button></span></label><label>Browser fallback<SelectField value={form.watch('youtube_cookies_browser') || ''} onValueChange={value => form.setValue('youtube_cookies_browser', value, { shouldDirty: true })} options={[{ value: '', label: 'Not used' }, { value: 'chrome', label: 'Google Chrome' }, { value: 'edge', label: 'Microsoft Edge' }, { value: 'firefox', label: 'Firefox' }, { value: 'brave', label: 'Brave' }]} /></label></section><section className="platform-access-row"><div className="platform-access-label"><strong>TikTok</strong><span>Fresh session for blocked requests</span></div><label>Cookies file<span className="file-input"><input placeholder="TikTok cookies.txt" {...form.register('tiktok_cookies_file')} /><button type="button" className="icon-button" onClick={() => chooseCookies('tiktok_cookies_file')} aria-label="Choose TikTok cookies.txt file"><FolderOpen size={16} /></button></span></label><span className="platform-access-note">Used only for TikTok crawl, metadata and downloads.</span></section></div></fieldset>
    </form>
  </section>
}
