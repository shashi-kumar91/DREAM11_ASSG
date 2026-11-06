import type { Pack } from '../lib/types'

interface ScoreboardProps {
  pack?: Pack
  scenario?: string
  atOver?: string
  score?: string
}

export function MatchScoreboard({ pack, scenario, atOver, score }: ScoreboardProps) {
  const currentScore = score || (scenario === 'S3' ? '149/5' : '147/5')
  const currentOver = atOver || (scenario === 'S3' ? '17.4' : '16.6')

  return (
    <div className="match-banner">
      <div className="match-live-tag">
        <span className="live-dot" />
        <span>LIVE · T20 CHASE · INNINGS 2</span>
        <span className="match-comp">IPL 2026 MATCH 48</span>
      </div>

      <div className="match-score-row">
        <div className="team-block">
          <div className="team-flag kestrels">CK</div>
          <div className="team-info">
            <div className="team-name">Chennai Kestrels</div>
            <div className="team-score">
              {currentScore} <span className="overs">({currentOver}/20 ov)</span>
            </div>
          </div>
        </div>

        <div className="match-equation">
          <div className="equation-text">
            Need <span className="highlight-runs">29 runs</span> off <span className="highlight-balls">18 balls</span>
          </div>
          <div className="rrr-badge">Required RR: 9.67 · Target: 176</div>
        </div>

        <div className="team-block opponent">
          <div className="team-info text-right">
            <div className="team-name">Mumbai Titans</div>
            <div className="team-score opponent-score">175/8 <span className="overs">(20 ov)</span></div>
          </div>
          <div className="team-flag titans">MT</div>
        </div>
      </div>

      <div className="match-batsmen-row">
        <div className="player-stat">
          <span className="role-dot on-strike" title="On strike" />
          <span className="player-name">Rohan Bedi</span>
          <span className="player-runs">8* (5b)</span>
        </div>
        <div className="player-stat">
          <span className="role-dot" />
          <span className="player-name">Pravin Rao</span>
          <span className="player-runs">0* (0b)</span>
        </div>
        <div className="bowler-stat">
          <span className="bowler-label">Bowler:</span>
          <span className="bowler-name">K. Nair</span>
          <span className="bowler-figures">3.0-0-24-2</span>
        </div>
        <div className="pack-context-chip">
          {pack === 'gap' ? '⚡ Gap Catch-Up Active' : pack === 'creator' ? '🎙️ Hinglish Stream Live' : '🏏 Dream11 Live Hub'}
        </div>
      </div>
    </div>
  )
}

