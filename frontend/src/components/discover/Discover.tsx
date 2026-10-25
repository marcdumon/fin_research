import { useResearch } from '../shell/useResearch'
import { Screens } from '../Screens'

export function Discover() {
  const research = useResearch()
  return (
    <div className="px-6 py-5">
      <Screens onResearch={research.ask} />
      {research.dialog}
      {research.error && <p className="mt-3 text-small text-danger">Could not start the run.</p>}
    </div>
  )
}
