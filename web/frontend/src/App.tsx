import { Routes, Route, NavLink } from 'react-router-dom'
import BacktestPage from './pages/Backtest'
import DashboardPage from './pages/Dashboard'
import OptimizerPage from './pages/Optimizer'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
          isActive
            ? 'bg-brand-500 text-white'
            : 'text-gray-600 hover:bg-gray-100'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Nav */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-2">
        <span className="text-brand-500 font-bold text-lg mr-4">📈 NexusTrader</span>
        <NavItem to="/" label="Dashboard" />
        <NavItem to="/backtest" label="Backtest" />
        <NavItem to="/optimizer" label="Optimizer" />
      </header>

      {/* Main */}
      <main className="flex-1 px-6 py-6 max-w-screen-2xl mx-auto w-full">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/optimizer" element={<OptimizerPage />} />
        </Routes>
      </main>
    </div>
  )
}
