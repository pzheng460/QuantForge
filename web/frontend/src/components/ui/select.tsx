import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * A native <select> styled to match the shadcn/ui design system.
 * We use a native select for simplicity and because the existing code
 * relies on native <option> children.
 */
const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => {
  return (
    <select
      className={cn(
        'flex h-9 w-full rounded-sm border border-input bg-background px-2 py-1 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer',
        className,
      )}
      ref={ref}
      {...props}
    >
      {children}
    </select>
  )
})
Select.displayName = 'Select'

export { Select }
