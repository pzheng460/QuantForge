import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium tracking-tight transition-colors',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-secondary text-secondary-foreground',
        secondary:
          'border-transparent bg-secondary text-secondary-foreground',
        destructive:
          'border-transparent bg-negative/10 text-negative ring-1 ring-inset ring-negative/15',
        outline:
          'border-border bg-card text-foreground',
        success:
          'border-transparent bg-positive/10 text-positive ring-1 ring-inset ring-positive/15',
        warning:
          'border-transparent bg-amber-500/10 text-amber-700 ring-1 ring-inset ring-amber-500/15',
        brand:
          'border-transparent bg-brand-soft text-brand ring-1 ring-inset ring-brand/15',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
