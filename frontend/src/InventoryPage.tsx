import { useEffect, useState } from 'react'
import { Alerts, Occupancy } from './AuditApp'
import type { AuditReport } from './audit-types'
import { InventoryEvidence } from './EvidenceAnalysis'

/** Inventory pages need snapshots only, never the older trip benchmark. */
export default function InventoryPage({ events }: { events: boolean }) {
  const [data, setData] = useState<Pick<AuditReport, 'occupancy'> | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    fetch('/audit-data/inventory.json', { signal: controller.signal }).then(async response => {
      if (response.ok) return response.json()
      // Existing historical exports can still supply the same occupancy contract.
      const legacy = await fetch('/audit-data/report.json', { signal: controller.signal })
      if (!legacy.ok) throw new Error('Historical inventory has not been published for this deployment.')
      return legacy.json()
    }).then(setData).catch(error => { if (!controller.signal.aborted) setError(String(error)) })
    return () => controller.abort()
  }, [])
  if (!data) return <main className="project-loading" role={error ? 'status' : undefined}>{error || 'Loading inventory…'}</main>
  if (!data.occupancy.rows) return <main className="project-loading">No readable inventory observations were published.</main>
  return <main className="a-main"><div className="a-content">{events ? <><InventoryEvidence /><Alerts data={data} /></> : <Occupancy data={data} />}</div></main>
}
