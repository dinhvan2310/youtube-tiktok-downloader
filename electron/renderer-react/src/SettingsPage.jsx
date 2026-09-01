import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderOpen, Gauge, LoaderCircle, MonitorDown, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { toast } from 'sonner'
import { getConfig, saveConfig } from './api'
import { SelectField } from './SelectField'

const numeric = ['quality_height', 'target_videos_per_page', 'source_thread_count', 'download_source_concurrency', 'global_download_concurrency', 'metadata_workers']

function SectionHeading({ icon: Icon, title, description }) {
  return <header className="settings-card-heading"><span><Icon size={18} aria-hidden="true" /></span><div><h2>{title}</h2><p>{description}</p></div></header>
}

function NumberSetting({ label, hint, register, name, min, max }) {
  return <label className="setting-field"><span><strong>{label}</strong><small>{hint}</small></span><input type="number" min={min} max={max} required {...register(name)} /></label>
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [saveError, setSaveError] = useState('')
  const { data: config, isPending } = useQuery({ queryKey: ['config'], queryFn: getConfig })
  const form = useForm({ values: config || {} })
  const save = useMutation({
    mutationFn: saveConfig,
    onSuccess: saved => {
      form.reset(saved)
      setSaveError('')
      queryClient.invalidateQueries({ queryKey: ['config'] })
      toast.success('Settings saved')
    },
    onError: error => setSaveError(error.message),
  })
  if (isPending) return <section className="page settings-page"><div className="table-state"><LoaderCircle className="spin" /> Loading settings…</div></section>

  const submit = values => {
    setSaveError('')
    save.mutate({ ...config, ...values, ...Object.fromEntries(numeric.map(key => [key, +values[key]])) })
  }
  const chooseCookies = async field => {
    const path = await window.desktop?.chooseFile([{ name: 'Netscape cookies', extensions: ['txt'] }, { name: 'All files', extensions: ['*'] }])
    if (path) form.setValue(field, path, { shouldDirty: true })
  }
  const dirty = form.formState.isDirty
  const tiktokCookiePath = String(form.watch('tiktok_cookies_file') || '').trim()

  return <section className="page settings-page">
    <header className="page-header settings-header"><div><p className="eyebrow">WORKSPACE</p><h1>Settings</h1><p>Quality, task capacity and platform access.</p></div><div className="settings-save"><span aria-live="polite">{dirty ? 'Unsaved changes' : 'All changes saved'}</span><button form="settings-form" type="submit" className="button button-primary" disabled={save.isPending || !dirty}>{save.isPending && <LoaderCircle className="spin" size={16} />}{save.isPending ? 'Saving…' : 'Save changes'}</button></div></header>
    {saveError && <div className="settings-error" role="alert"><strong>Could not save settings</strong><span>{saveError}</span></div>}
    <form id="settings-form" className="settings-layout" onSubmit={form.handleSubmit(submit)}>
      <section className="settings-card">
        <SectionHeading icon={MonitorDown} title="Download quality" description="Set the output target and when a source is considered full." />
        <div className="settings-fields two-columns">
          <label className="setting-field"><span><strong>Maximum resolution</strong><small>No limit downloads the best format available.</small></span><SelectField value={String(form.watch('quality_height') ?? 0)} onValueChange={value => form.setValue('quality_height', value, { shouldDirty: true })} options={[{ value: '0', label: 'No limit (best available)' }, { value: '720', label: '720p' }, { value: '1080', label: '1080p' }, { value: '1440', label: '1440p' }]} /></label>
          <NumberSetting label="Videos per source" hint="Stop adding downloads when this folder target is reached." register={form.register} name="target_videos_per_page" min="1" />
        </div>
      </section>

      <section className="settings-card">
        <SectionHeading icon={Gauge} title="Task capacity" description="Control parallel work. New limits apply to tasks started after saving." />
        <div className="settings-fields three-columns">
          <NumberSetting label="Crawl sources" hint="Channels scanned at the same time." register={form.register} name="source_thread_count" min="1" max="8" />
          <NumberSetting label="Download sources" hint="Source folders downloading at once." register={form.register} name="download_source_concurrency" min="1" max="8" />
          <NumberSetting label="Global downloads" hint="Maximum simultaneous videos across all sources." register={form.register} name="global_download_concurrency" min="1" max="32" />
        </div>
        <details className="advanced-settings"><summary><SlidersHorizontal size={15} aria-hidden="true" /><span><strong>Advanced processing</strong><small>Metadata enrichment workers</small></span></summary><div><NumberSetting label="Metadata workers" hint="Parallel title, thumbnail and engagement lookups." register={form.register} name="metadata_workers" min="1" max="16" /></div></details>
      </section>

      <section className="settings-card platform-settings">
        <SectionHeading icon={ShieldCheck} title="Platform access" description="Cookies are isolated per platform and only used for crawl, metadata and downloads." />
        <div className="credential-list">
          <section className="credential-row"><div className="credential-platform"><strong>YouTube</strong><small>Verification and restricted videos</small></div><label className="setting-field"><span><strong>Cookies file</strong><small>Recommended when browser cookie access is locked.</small></span><span className="file-input"><input placeholder="YouTube cookies.txt" {...form.register('youtube_cookies_file')} /><button type="button" className="icon-button" onClick={() => chooseCookies('youtube_cookies_file')} aria-label="Choose YouTube cookies file"><FolderOpen size={16} /></button></span></label><label className="setting-field"><span><strong>Browser fallback</strong><small>Used only when no cookies file is selected.</small></span><SelectField value={form.watch('youtube_cookies_browser') || ''} onValueChange={value => form.setValue('youtube_cookies_browser', value, { shouldDirty: true })} options={[{ value: '', label: 'Not used' }, { value: 'chrome', label: 'Google Chrome' }, { value: 'edge', label: 'Microsoft Edge' }, { value: 'firefox', label: 'Firefox' }, { value: 'brave', label: 'Brave' }]} /></label></section>
          <section className="credential-row"><div className="credential-platform"><strong>TikTok</strong><small>Fresh session for blocked requests</small></div><label className="setting-field credential-wide"><span><strong>Cookies file</strong><small>Use a Netscape cookies.txt exported while signed in to TikTok.</small></span><span className="file-input"><input placeholder="TikTok cookies.txt" {...form.register('tiktok_cookies_file')} /><button type="button" className="icon-button" onClick={() => chooseCookies('tiktok_cookies_file')} aria-label="Choose TikTok cookies file"><FolderOpen size={16} /></button></span><small className={`credential-status ${tiktokCookiePath ? (dirty ? 'is-pending' : 'is-ready') : 'is-empty'}`} role="status">{tiktokCookiePath ? (dirty ? 'File selected — save settings to apply.' : 'File configured.') : 'No cookie file configured.'}</small></label></section>
        </div>
      </section>
    </form>
  </section>
}
