import { lazy, Suspense, useEffect, useRef, useState, type MouseEvent } from 'react'
import './Navigation.css'
import { datasetId } from './dataSource'

const Project = lazy(() => import('./ProjectApp'))
const Inventory = lazy(() => import('./InventoryPage'))
const Explorer = lazy(() => import('./App'))
const Research = lazy(() => import('./ResearchPage'))
const items = [
  { id: 'overview', title: 'Overview', group: 'Explore', icon: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z' },
  { id: 'weather', title: 'Weather & usage', group: 'Explore', icon: 'M7 15a5 5 0 1 1 8-7 4 4 0 1 1 2 7H7 M8 18l-1 3 M13 18l-1 3 M18 18l-1 3' },
  { id: 'forecasts', title: 'Forecast explorer', group: 'Explore', icon: 'M3 4v17h18 M6 15l4-6 4 3 6-8' },
  { id: 'occupancy', title: 'Station occupancy', group: 'Explore', icon: 'M4 20V9l8-6 8 6v11H4z M9 20v-7h6v7' },
  { id: 'events', title: 'Empty & full events', group: 'Explore', icon: 'M12 3L2 21h20L12 3z M12 9v5 M12 17v1' },
  { id: 'models', title: 'Model comparison', group: 'Research', icon: 'M4 20V10h4v10 M10 20V4h4v16 M16 20V7h4v13' },
  { id: 'quality', title: 'Data & provenance', group: 'Research', icon: 'M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0' },
  { id: 'methods', title: 'Methods & limitations', group: 'Research', icon: 'M5 3h11l3 3v15H5V3z M9 9h6 M9 13h6 M9 17h4' },
  { id: 'explorer', title: 'Database explorer', group: 'Advanced', icon: 'M3 5h18v14H3V5z M7 9l3 3-3 3 M13 15h4' },
] as const
type Page = typeof items[number]['id']
const aliases: Record<string, Page> = { model: 'models', 'model-comparison': 'models', 'monthly-weather': 'weather', replay: 'forecasts', alerts: 'events', findings: 'methods', 'inventory-analysis': 'occupancy' }
function pageFrom(url: URL): Page {
  if (url.searchParams.get('view') === 'explorer') return 'explorer'
  const hash = url.hash.slice(1)
  return items.find(item => item.id === hash)?.id ?? aliases[hash] ?? 'overview'
}

export default function RootApp() {
  const [datasets, setDatasets] = useState<{ id: string; name: string; city: string; year: number }[]>([])
  const [page, setPage] = useState<Page>(() => pageFrom(new URL(location.href)))
  const [menuOpen, setMenuOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const current = items.find(item => item.id === page)!
  const dataset = datasets.find(item => item.id === datasetId())
  useEffect(() => {
    const controller = new AbortController()
    fetch('/project-data/catalog.json', { signal: controller.signal }).then(r => r.ok ? r.json() : null).then(value => {
      if (!value?.datasets?.length) return
      setDatasets(value.datasets)
      if (!datasetId()) {
        const url = new URL(location.href)
        url.searchParams.set('dataset', value.datasets[0].id)
        location.replace(url)
      }
    }).catch(() => { /* Older standalone exports remain readable without a catalog. */ })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (new URLSearchParams(location.search).has('view')) history.replaceState(null, '', `${location.pathname}#${pageFrom(new URL(location.href))}`)
    const sync = () => { setPage(pageFrom(new URL(location.href))); setMenuOpen(false) }
    window.addEventListener('popstate', sync)
    window.addEventListener('hashchange', sync)
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync) }
  }, [])
  useEffect(() => {
    document.title = `${current.title} · MobilityLab`
    window.scrollTo({ top: 0, behavior: 'instant' })
    content.current?.focus({ preventScroll: true })
  }, [current.title])
  // Links remain copyable and support new tabs; ordinary clicks share one router.
  function navigate(event: MouseEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const link = (event.target as Element).closest<HTMLAnchorElement>('a[href]')
    if (!link || link.hasAttribute('download') || link.target === '_blank') return
    const url = new URL(link.href, location.href)
    if (url.origin !== location.origin || url.pathname !== location.pathname) return
    const hash = url.hash.slice(1)
    if (hash && !items.some(item => item.id === hash) && !aliases[hash]) return
    if (!hash && !url.searchParams.has('view') && link.getAttribute('href') !== '/') return
    event.preventDefault()
    const next = pageFrom(url)
    if (location.hash !== `#${next}`) history.pushState(null, '', `${location.pathname}${location.search}#${next}`)
    setPage(next)
    setMenuOpen(false)
    if (page === next) { window.scrollTo({ top: 0, behavior: 'instant' }); content.current?.focus({ preventScroll: true }) }
  }
  return <div className="site-shell" onClick={navigate} onKeyDown={event => { if (event.key === 'Escape' && menuOpen) { setMenuOpen(false); menuButton.current?.focus() } }}>
    <a className="site-skip" href="#workspace-content" onClick={event => { event.preventDefault(); content.current?.focus(); content.current?.scrollIntoView() }}>Skip to content</a>
    <header className="site-mobile-header"><a href="#overview">MobilityLab</a><span>{current.title}</span><button ref={menuButton} aria-controls="global-navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? 'Close menu' : 'Menu'} <span aria-hidden="true">{menuOpen ? '×' : '☰'}</span></button></header>
    <aside className={`site-sidebar ${menuOpen ? 'is-open' : ''}`} id="global-navigation" onKeyDown={event => { if (event.key === 'Escape') { setMenuOpen(false); menuButton.current?.focus() } }}>
      <a className="site-brand" href="#overview"><span className="site-mark">m<span>·</span></span><span>MobilityLab<small>{dataset ? `${dataset.city} · ${dataset.name}` : 'Mobility research'}</small></span></a>
      {datasets.length > 0 && <label className="site-dataset-selector">Dataset<select aria-label="Provider and year" value={datasetId() || ''} onChange={event => { const url = new URL(location.href); url.searchParams.set('dataset', event.target.value); location.assign(url) }}>{datasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.year}</option>)}</select></label>}
      <nav aria-label="Global navigation">{['Explore','Research','Advanced'].map(group => <div className="site-nav-group" key={group}><p>{group}</p>{items.filter(item => item.group === group).map(item => <a key={item.id} href={`#${item.id}`} aria-current={page === item.id ? 'page' : undefined}><svg viewBox="0 0 24 24" aria-hidden="true"><path d={item.icon} /></svg><span>{item.title}</span>{page === item.id && <i aria-hidden="true" />}</a>)}</div>)}</nav>
      <div className="site-sidebar-footer"><span><i /> Historical research</span><small>Recorded journeys, weather<br/>and station observations.</small></div>
    </aside>
    <div className="site-content" id="workspace-content" ref={content} tabIndex={-1}>
      <div className="site-location"><span>{current.group} <b aria-hidden="true">/</b> <strong>{current.title}</strong></span><span className="site-context">{dataset?.city || 'Historical'} mobility research</span></div>
      <Suspense fallback={<p className="site-loading" role="status">Loading {current.title.toLowerCase()}…</p>}>
        {page === 'overview' || page === 'weather' || page === 'forecasts' || page === 'quality' ? <Project section={page} /> : page === 'models' || page === 'methods' ? <Research view={page} /> : page === 'explorer' ? <Explorer /> : datasetId() && !datasetId()?.startsWith('bicimad/madrid/') ? <p className="project-loading">Historical inventory is not published for this provider.</p> : <Inventory events={page === 'events'} />}
      </Suspense>
    </div>
  </div>
}
