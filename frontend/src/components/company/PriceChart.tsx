import { CandlestickSeries, createChart, createSeriesMarkers, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { PriceSeries } from '../../api'

// The chart reads the CSS tokens rather than carrying its own palette, so it follows the theme
// switch instead of staying dark on a light page.
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export function PriceChart({ series, theme }: { series: PriceSeries; theme: string }) {
  const host = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!host.current || series.bars.length === 0) return

    const made = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { color: 'transparent' },
        textColor: token('--color-muted'),
        fontFamily: token('--font-sans'),
        fontSize: 11,
      },
      grid: {
        vertLines: { color: token('--color-rule') },
        horzLines: { color: token('--color-rule') },
      },
      rightPriceScale: { borderColor: token('--color-rule') },
      timeScale: { borderColor: token('--color-rule'), rightOffset: 4 },
      crosshair: { mode: 0 },
    })

    const candles = made.addSeries(CandlestickSeries, {
      upColor: token('--color-up'),
      downColor: token('--color-down'),
      borderVisible: false,
      wickUpColor: token('--color-up'),
      wickDownColor: token('--color-down'),
    })
    candles.setData(series.bars)

    // The markers are the reason this is worth charting rather than tabulating: a move is a
    // question, and the filing nearest it is usually where the answer was written down.
    //
    // Only the periodic reports are named. A company files an 8-K for anything material, and VSAT has 219
    // of them: labelling each one wrote the word '8-K' across the price history until neither was legible.
    // They stay as unlabelled ticks, because when they cluster around a move that clustering is the point.
    const periodic = (form: string) => form.startsWith('10-K') || form.startsWith('10-Q')
    createSeriesMarkers(candles, series.markers.map((marker) => ({
      time: marker.time,
      position: periodic(marker.form) ? 'aboveBar' as const : 'belowBar' as const,
      color: periodic(marker.form) ? token('--color-accent') : token('--color-faint'),
      shape: periodic(marker.form) ? 'arrowDown' as const : 'circle' as const,
      size: periodic(marker.form) ? 1 : 0.5,
      text: periodic(marker.form) ? marker.form : '',
    })))

    made.timeScale().fitContent()
    chart.current = made
    return () => { made.remove(); chart.current = null }
  }, [series, theme])

  if (!series.available) {
    return (
      <p className="text-small text-faint">
        No price history. The market database is not attached.
      </p>
    )
  }
  if (series.bars.length === 0) {
    return <p className="text-small text-faint">No prices for this company in the market database.</p>
  }

  return <div ref={host} className="h-80 w-full" />
}
