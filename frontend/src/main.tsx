import { createRoot } from 'react-dom/client'
import { App } from './routes/App'

/**
 * No StrictMode on purpose: its dev-only double mount opens a second EventSource against the
 * same session id and the journal would replay twice. The production bundle never mounts twice,
 * and the journal-catch-up test reloads the page instead.
 */
const host = document.getElementById('root')
if (!host) throw new Error('index.html has no #root')
createRoot(host).render(<App />)
