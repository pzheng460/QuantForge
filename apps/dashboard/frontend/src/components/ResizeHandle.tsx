import { cn } from '@/lib/utils'
import { useLang } from '../i18n'

type HandleProps = {
  onPointerDown?: (e: React.PointerEvent<HTMLDivElement>) => void
  onPointerMove?: (e: React.PointerEvent<HTMLDivElement>) => void
  onPointerUp?: (e: React.PointerEvent<HTMLDivElement>) => void
  onPointerCancel?: (e: React.PointerEvent<HTMLDivElement>) => void
}

interface Props {
  orientation: 'horizontal' | 'vertical'
  /** Spread all pointer handlers from `useResizablePanel().handleProps` here. */
  handleProps?: HandleProps
  /** Legacy single-callback API (still used by ResizableSidebarShell). */
  onPointerDown?: (e: React.PointerEvent<HTMLDivElement>) => void
  onDoubleClick?: () => void
  className?: string
  style?: React.CSSProperties
}

/**
 * Resize handle for column ("horizontal" axis between L/R panels) or row
 * ("vertical" axis between top/bottom panels) splits.
 *
 * Drag mechanics use Pointer Events with setPointerCapture, ensuring all
 * subsequent move/up events arrive at THIS element regardless of what's
 * physically under the cursor — sibling pointer-capturing widgets like
 * lightweight-charts cannot steal the drag.
 */
export function ResizeHandle({
  orientation,
  handleProps,
  onPointerDown,
  onDoubleClick,
  className,
  style,
}: Props) {
  const { t } = useLang()
  const isCol = orientation === 'horizontal'

  return (
    <div
      role="separator"
      aria-orientation={isCol ? 'vertical' : 'horizontal'}
      onDoubleClick={onDoubleClick}
      style={{ touchAction: 'none', ...style }}
      // handleProps fully overrides the legacy onPointerDown if both are passed.
      {...(handleProps ?? { onPointerDown })}
      className={cn(
        'group relative shrink-0 z-30',
        isCol ? 'w-2 cursor-col-resize' : 'h-2.5 cursor-row-resize',
        className,
      )}
      title={t("app.resizeTitle")}
    >
      <div
        className={cn(
          'absolute bg-border transition-all duration-150',
          'group-hover:bg-brand group-active:bg-brand',
          isCol
            ? 'left-1/2 -translate-x-1/2 top-0 bottom-0 w-[2px] group-hover:w-[3px]'
            : 'top-1/2 -translate-y-1/2 left-0 right-0 h-[2px] group-hover:h-[3px]',
        )}
      />
      {/* Grip dots — affordance on hover */}
      <div
        aria-hidden
        className={cn(
          'absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center justify-center gap-[3px] pointer-events-none',
          'opacity-0 group-hover:opacity-100 transition-opacity duration-150',
          isCol ? 'flex-col' : 'flex-row',
        )}
      >
        <span className="w-[3px] h-[3px] rounded-full bg-brand" />
        <span className="w-[3px] h-[3px] rounded-full bg-brand" />
        <span className="w-[3px] h-[3px] rounded-full bg-brand" />
      </div>
    </div>
  )
}
