import { useState, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { AgentEvent } from '../types'

interface AgentTraceViewerProps {
  events: AgentEvent[]
  status: string
  className?: string
}

function EventIcon({ type, toolName }: { type: string; toolName?: string }) {
  if (type === 'thinking') return <span className="text-blue-400">💭</span>
  if (type === 'tool_call') {
    if (toolName === 'Read') return <span className="text-green-400">📖</span>
    if (toolName === 'Edit') return <span className="text-yellow-400">📝</span>
    if (toolName === 'Write') return <span className="text-purple-400">📄</span>
    if (toolName === 'Bash') return <span className="text-orange-400">⚡</span>
    return <span className="text-muted-foreground">🔧</span>
  }
  if (type === 'tool_result') return <span className="text-muted-foreground/70">↩️</span>
  if (type === 'error') return <span className="text-red-400">❌</span>
  if (type === 'done') return <span className="text-green-400">✅</span>
  return <span className="text-muted-foreground">•</span>
}

function ThinkingEvent({
  event,
  expanded,
  onToggle,
}: {
  event: AgentEvent
  expanded: boolean
  onToggle: () => void
}) {
  const lines = event.content.split('\n')
  const previewLines = lines.slice(0, 3)
  const hasMore = lines.length > 3

  return (
    <div className="border border-border rounded-lg p-3 bg-card">
      <div className="flex items-start gap-2">
        <EventIcon type={event.type} toolName={event.tool_name} />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-foreground leading-relaxed">
            {expanded ? (
              <pre className="whitespace-pre-wrap font-mono text-xs">{event.content}</pre>
            ) : (
              <>
                {previewLines.map((line, i) => (
                  <div key={i}>{line}</div>
                ))}
                {lines.length > 3 && (
                  <Button
                    variant="link"
                    size="sm"
                    onClick={onToggle}
                    className="text-primary hover:text-primary/80 text-xs mt-1 font-medium h-auto p-0"
                  >
                    Show more...
                  </Button>
                )}
              </>
            )}
          </div>
          {expanded && hasMore && (
            <Button
              variant="link"
              size="sm"
              onClick={onToggle}
              className="text-primary hover:text-primary/80 text-xs mt-2 font-medium h-auto p-0"
            >
              Show less
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function ToolCallEvent({
  event,
  expanded,
  onToggle,
}: {
  event: AgentEvent
  expanded: boolean
  onToggle: () => void
}) {
  const { tool_name, file_path, diff, content } = event

  if (tool_name === 'Edit' && diff) {
    return (
      <div className="border border-border rounded-lg p-3 bg-card">
        <div className="flex items-center gap-2 mb-2">
          <EventIcon type={event.type} toolName={event.tool_name} />
          <span className="text-sm font-medium text-foreground">Edit: {file_path}</span>
        </div>
        <div className="bg-background border border-border rounded overflow-hidden">
          <div className="bg-red-900 bg-opacity-20 p-2">
            <div className="text-red-300 text-xs font-medium mb-1">- Removed</div>
            <pre className="text-xs text-red-200 whitespace-pre-wrap">{diff.old}</pre>
          </div>
          <div className="bg-green-900 bg-opacity-20 p-2">
            <div className="text-green-300 text-xs font-medium mb-1">+ Added</div>
            <pre className="text-xs text-green-200 whitespace-pre-wrap">{diff.new}</pre>
          </div>
        </div>
      </div>
    )
  }

  if (tool_name === 'Bash') {
    return (
      <div className="border border-border rounded-lg p-3 bg-card">
        <div className="flex items-center gap-2">
          <EventIcon type={event.type} toolName={event.tool_name} />
          <span className="text-sm text-foreground font-mono">{content}</span>
        </div>
      </div>
    )
  }

  if (tool_name === 'Read') {
    return (
      <div className="border border-border rounded-lg p-3 bg-card">
        <div className="flex items-center gap-2">
          <EventIcon type={event.type} toolName={event.tool_name} />
          <span className="text-sm text-foreground">Read: {file_path}</span>
        </div>
      </div>
    )
  }

  if (tool_name === 'Write') {
    return (
      <div className="border border-border rounded-lg p-3 bg-card">
        <div className="flex items-start gap-2">
          <EventIcon type={event.type} toolName={event.tool_name} />
          <div className="flex-1 min-w-0">
            <div className="text-sm text-foreground mb-2">Write: {file_path}</div>
            {expanded && (
              <div className="bg-background border border-border rounded p-2 max-h-96 overflow-auto">
                <pre className="text-xs text-foreground whitespace-pre-wrap">{content}</pre>
              </div>
            )}
            <Button
              variant="link"
              size="sm"
              onClick={onToggle}
              className="text-primary hover:text-primary/80 text-xs mt-1 font-medium h-auto p-0"
            >
              {expanded ? 'Hide content' : 'Show content'}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // Generic tool call
  return (
    <div className="border border-border rounded-lg p-3 bg-card">
      <div className="flex items-start gap-2">
        <EventIcon type={event.type} toolName={event.tool_name} />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-foreground font-medium mb-1">{tool_name}</div>
          <pre className="text-xs text-muted-foreground whitespace-pre-wrap">{content}</pre>
        </div>
      </div>
    </div>
  )
}

function ToolResultEvent({
  event,
  expanded,
  onToggle,
}: {
  event: AgentEvent
  expanded: boolean
  onToggle: () => void
}) {
  const lines = event.content.split('\n')
  const isLong = lines.length > 10 || event.content.length > 1000

  return (
    <div className="border border-border rounded-lg bg-background">
      <div className="p-3">
        <div className="flex items-center gap-2 mb-2">
          <EventIcon type={event.type} />
          <span className="text-sm text-muted-foreground">Output</span>
        </div>
        <div
          className={cn(
            'bg-black rounded font-mono text-xs text-green-300 p-2 overflow-auto',
            !expanded && isLong && 'max-h-32',
          )}
        >
          <pre className="whitespace-pre-wrap">{event.content}</pre>
        </div>
        {isLong && (
          <Button
            variant="link"
            size="sm"
            onClick={onToggle}
            className="text-primary hover:text-primary/80 text-xs mt-2 font-medium h-auto p-0"
          >
            {expanded ? 'Show less' : 'Expand'}
          </Button>
        )}
      </div>
    </div>
  )
}

function ErrorEvent({ event }: { event: AgentEvent }) {
  return (
    <div className="border border-red-500 rounded-lg p-3 bg-red-900 bg-opacity-20">
      <div className="flex items-start gap-2">
        <EventIcon type={event.type} />
        <div className="flex-1">
          <div className="text-red-300 text-sm font-medium mb-1">Error</div>
          <pre className="text-red-200 text-xs whitespace-pre-wrap">{event.content}</pre>
        </div>
      </div>
    </div>
  )
}

function DoneEvent({ event }: { event: AgentEvent }) {
  return (
    <div className="border border-green-500 rounded-lg p-3 bg-green-900 bg-opacity-20">
      <div className="flex items-center gap-2">
        <EventIcon type={event.type} />
        <span className="text-green-300 text-sm font-medium">{event.content}</span>
      </div>
    </div>
  )
}

export default function AgentTraceViewer({
  events,
  status,
  className,
}: {
  events: AgentEvent[]
  status: string
  className?: string
}) {
  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set())
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevEventsLength = useRef(events.length)

  const toggleExpanded = (index: number) => {
    const next = new Set(expandedEvents)
    if (next.has(index)) {
      next.delete(index)
    } else {
      next.add(index)
    }
    setExpandedEvents(next)
  }

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (autoScroll && events.length > prevEventsLength.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
    prevEventsLength.current = events.length
  }, [events.length, autoScroll])

  // Detect when user scrolls up
  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    const isNearBottom = scrollTop + clientHeight >= scrollHeight - 50
    setAutoScroll(isNearBottom)
  }

  return (
    <div className={cn('flex flex-col', className)}>
      {/* Header bar */}
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="text-sm font-medium text-foreground">
            Agent Status:{' '}
            <span
              className={cn(
                'capitalize',
                status === 'running'
                  ? 'text-blue-400'
                  : status === 'completed'
                    ? 'text-green-400'
                    : status === 'failed'
                      ? 'text-red-400'
                      : 'text-muted-foreground',
              )}
            >
              {status}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">{events.length} events</div>
        </div>
        {!autoScroll && (
          <Button
            size="sm"
            onClick={() => {
              setAutoScroll(true)
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight
              }
            }}
          >
            Resume auto-scroll
          </Button>
        )}
      </div>

      {/* Events list */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-3"
        onScroll={handleScroll}
      >
        {events.length === 0 ? (
          <div className="text-center text-muted-foreground py-8">
            {status === 'pending' ? 'Waiting for agent to start...' : 'No events yet'}
          </div>
        ) : (
          events.map((event, index) => {
            const expanded = expandedEvents.has(index)
            const toggle = () => toggleExpanded(index)

            if (event.type === 'thinking') {
              return (
                <ThinkingEvent
                  key={index}
                  event={event}
                  expanded={expanded}
                  onToggle={toggle}
                />
              )
            }

            if (event.type === 'tool_call') {
              return (
                <ToolCallEvent
                  key={index}
                  event={event}
                  expanded={expanded}
                  onToggle={toggle}
                />
              )
            }

            if (event.type === 'tool_result') {
              return (
                <ToolResultEvent
                  key={index}
                  event={event}
                  expanded={expanded}
                  onToggle={toggle}
                />
              )
            }

            if (event.type === 'error') {
              return <ErrorEvent key={index} event={event} />
            }

            if (event.type === 'done') {
              return <DoneEvent key={index} event={event} />
            }

            return null
          })
        )}

        {status === 'running' && (
          <div className="flex items-center gap-2 text-muted-foreground py-4">
            <div className="animate-spin w-4 h-4 border-2 border-primary border-t-transparent rounded-full" />
            <span className="text-sm">Agent is working...</span>
          </div>
        )}
      </div>
    </div>
  )
}
