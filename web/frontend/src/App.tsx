import { Routes, Route, NavLink } from 'react-router-dom'
import BacktestPage from './pages/Backtest'
import DashboardPage from './pages/Dashboard'
import OptimizerPage from './pages/Optimizer'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `px-3 py-1.5 rounded-sm text-xs font-medium transition-colors ${
          isActive
            ? 'bg-tv-blue text-white'
            : 'text-tv-muted hover:text-tv-text hover:bg-tv-border'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-tv-bg">
      {/* TV-style top nav bar */}
      <header className="bg-tv-panel border-b border-tv-border px-4 py-2 flex items-center gap-1 shrink-0">
        <span className="text-tv-blue font-bold text-sm mr-4 flex items-center gap-1">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/>
          </svg>
          NexusTrader
        </span>
        <div className="flex items-center gap-0.5">
          <NavItem to="/" label="Dashboard" />
          <NavItem to="/backtest" label="Backtest" />
          <NavItem to="/optimizer" label="Optimizer" />
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={
            <div className="px-6 py-6 max-w-screen-2xl mx-auto w-full">
              <DashboardPage />
            </div>
          } />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/optimizer" element={
            <div className="px-6 py-6 max-w-screen-2xl mx-auto w-full">
              <OptimizerPage />
            </div>
          } />
        </Routes>
      </main>
    </div>
  )
}
