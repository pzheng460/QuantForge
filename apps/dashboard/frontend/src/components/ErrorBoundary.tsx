import { Component, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { useLang } from '../i18n'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

function ErrorFallback(props: { message: string; stack: string; onRetry: () => void }) {
  const { t } = useLang()
  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="text-center max-w-md px-6">
        <div className="text-4xl mb-4">:(</div>
        <h1 className="text-lg font-semibold text-foreground mb-2">{t('app.errTitle')}</h1>
        <p className="text-sm text-muted-foreground mb-1">
          {props.message || t('app.errMsg')}
        </p>
        {props.stack && (
          <pre className="text-xs text-muted-foreground/60 mb-6 max-h-32 overflow-auto text-left bg-muted p-3 rounded-md">
            {props.stack.split('\n').slice(0, 5).join('\n')}
          </pre>
        )}
        <Button onClick={props.onRetry}>{t('app.errTryAgain')}</Button>
      </div>
    </div>
  )
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <ErrorFallback
          message={this.state.error?.message ?? ''}
          stack={this.state.error?.stack ?? ''}
          onRetry={() => this.setState({ hasError: false, error: null })}
        />
      )
    }
    return this.props.children
  }
}
