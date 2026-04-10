import { useEffect, useRef } from 'react'
import {
  createChart,
  ColorType,
  LineStyle,
  AreaSeries,
  LineSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import type { EquityPoint, TradeRecord } from '../../types'

interface Props {
  equityCurve: EquityPoint[]
  trades: TradeRecord[]
  height?: number
}

function toUnixSec(isoString: string): number {
  return Math.floor(new Date(isoString).getTime() / 1000)
}

export default function TradingChart({ equityCurve, trades, height = 400 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const strategySeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const bhSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  // Initialize chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#131722' },
        textColor: '#787b86',
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1e222d', style: LineStyle.Solid },
        horzLines: { color: '#1e222d', style: LineStyle.Solid },
      },
      crosshair: {
        vertLine: { color: '#758696', width: 1, style: LineStyle.Dashed },
        horzLine: { color: '#758696', width: 1, style: LineStyle.Dashed },
      },
      rightPriceScale: {
        borderColor: '#2a2e39',
      },
      timeScale: {
        borderColor: '#2a2e39',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
      width: containerRef.current.clientWidth,
      height: height ?? (containerRef.current.clientHeight || 400),
    })

    // Strategy equity area series
    const strategySeries = chart.addSeries(AreaSeries, {
      lineColor: '#2962ff',
      topColor: 'rgba(41, 98, 255, 0.3)',
      bottomColor: 'rgba(41, 98, 255, 0.0)',
      lineWidth: 2,
      title: 'Strategy',
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    })

    // Buy & Hold line series
    const bhSeries = chart.addSeries(LineSeries, {
      color: '#f59f00',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      title: 'B&H',
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    })

    chartRef.current = chart
    strategySeriesRef.current = strategySeries
    bhSeriesRef.current = bhSeries

    // Handle resize (both width and height)
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height: h } = entry.contentRect
        if (width > 0 && h > 0) {
          chart.applyOptions({ width, height: h })
        }
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      strategySeriesRef.current = null
      bhSeriesRef.current = null
    }
  }, [height])

  // Update data when equityCurve changes
  const hasFittedRef = useRef(false)
  useEffect(() => {
    if (!strategySeriesRef.current || !bhSeriesRef.current || !equityCurve.length) return

    // Build time-series data, deduplicate and sort by time
    const seenTimes = new Set<number>()
    const stratData: { time: Time; value: number }[] = []
    const bhData: { time: Time; value: number }[] = []

    for (const pt of equityCurve) {
      const t = toUnixSec(pt.t) as Time
      if (seenTimes.has(t as number)) continue
      seenTimes.add(t as number)
      stratData.push({ time: t, value: pt.strategy })
      bhData.push({ time: t, value: pt.bh })
    }

    stratData.sort((a, b) => (a.time as number) - (b.time as number))
    bhData.sort((a, b) => (a.time as number) - (b.time as number))

    strategySeriesRef.current.setData(stratData)
    bhSeriesRef.current.setData(bhData)

    // Add trade markers using v5 createSeriesMarkers
    if (trades.length > 0 && stratData.length > 0) {
      const firstTime = stratData[0].time as number
      const lastTime = stratData[stratData.length - 1].time as number

      const markers: SeriesMarker<Time>[] = trades
        .filter((tr) => {
          const t = toUnixSec(tr.timestamp)
          return t >= firstTime && t <= lastTime
        })
        .map((tr) => {
          const isBuy = tr.side === 'buy'
          return {
            time: toUnixSec(tr.timestamp) as Time,
            position: (isBuy ? 'belowBar' : 'aboveBar') as 'belowBar' | 'aboveBar',
            color: isBuy ? '#26a69a' : '#ef5350',
            shape: (isBuy ? 'arrowUp' : 'arrowDown') as 'arrowUp' | 'arrowDown',
            text: isBuy ? 'B' : 'S',
            size: 1,
          }
        })
        .sort((a, b) => (a.time as number) - (b.time as number))

      // Deduplicate markers by (time, position) key
      const dedupedMarkers: SeriesMarker<Time>[] = []
      const seenMarkerTimes = new Set<string>()
      for (const m of markers) {
        const key = `${m.time as number}-${m.position}`
        if (!seenMarkerTimes.has(key)) {
          seenMarkerTimes.add(key)
          dedupedMarkers.push(m)
        }
      }

      createSeriesMarkers(strategySeriesRef.current, dedupedMarkers)
    }

    // Only fit content on first data load — avoid jumping on live updates
    if (!hasFittedRef.current) {
      chartRef.current?.timeScale().fitContent()
      hasFittedRef.current = true
    }
  }, [equityCurve, trades])

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      {/* Legend overlay */}
      <div className="absolute top-2 left-3 flex items-center gap-4 pointer-events-none">
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="inline-block w-5 h-0.5 bg-[#2962ff]" />
          Strategy
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="inline-block w-5 border-t border-dashed border-[#f59f00]" />
          Buy &amp; Hold
        </span>
        <span className="flex items-center gap-1 text-xs text-tv-green">▲ Buy</span>
        <span className="flex items-center gap-1 text-xs text-tv-red">▼ Sell</span>
      </div>
    </div>
  )
}
