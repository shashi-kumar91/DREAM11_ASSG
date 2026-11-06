import type { Facts } from '../lib/facts'
import type { Card, PlayerMode, Scenario } from '../lib/types'
import type { SessionView } from '../lib/useSession'
import { BallStrip, FramesGallery, MatchBar } from '../components/match'
import { Drawer, Empty, Photo, Pill, Reveal, Section, Step } from '../components/bits'
import { CardList } from '../components/cards'

interface HomeProps {
  facts: Facts | null
  factsError: string | null
  manifest: {
    run_mode: string
    backend: string
    cassettes: number
  } | null
  view: SessionView
  mode: PlayerMode
  onMode: (m: PlayerMode) => void
  go: (to: 'home' | 'gap' | 'creator') => void
  launch: (pack: 'gap' | 'creator', scenario: string) => void
  scenarios: { gap: Scenario[]; creator: Scenario[] }
}

/** One demo card: what happened to you, what the agent will do, and a single button. */
function DemoCard({
  scenario,
  icon,
  title,
  what,
  agentDoes,
  onRun,
  delay,
}: {
  scenario: string
  icon: string
  title: string
  what: string
  agentDoes: string
  onRun: () => void
  delay: number
}) {
  return (
    <Reveal delay={delay} className="demo-card">
      <div className="demo-top">
        <span className="demo-icon">{icon}</span>
        <span className="demo-id">{scenario}</span>
      </div>
      <h3>{title}</h3>
      <p className="demo-what">{what}</p>
      <p className="demo-agent">
        <b>What the agent does:</b> {agentDoes}
      </p>
      <button type="button" className="btn primary full" onClick={onRun}>
        Run this situation →
      </button>
    </Reveal>
  )
}

export function HomeRoute(props: HomeProps) {
  const { facts, factsError, manifest, view, go, launch } = props

  if (!facts) {
    return (
      <div className="loading-wrap">
        <div className="skeleton hero-skeleton" />
        <div className="skeleton" />
        <div className="skeleton short" />
        {factsError ? <p className="err-box">Could not load /api/facts — {factsError}</p> : null}
      </div>
    )
  }

  const credit = (name: string) => facts.photo_credits.find((c) => c.file.endsWith(`${name}.jpg`))
  const feed = facts.feed
  const recentCards: Card[] = view.cards.slice(-2)

  return (
    <div className="home">
      {/* ---------------------------------------------------------------- hero */}
      <header className="hero">
        <Photo
          src="/img/hero-stadium.jpg"
          alt="A floodlit cricket stadium seen from the stands, the crowd filling the upper tiers"
          ratio="16 / 8"
          className="hero-photo"
          credit={credit('hero-stadium')}
        />
        <div className="hero-veil" />
        <div className="hero-copy">
          <Pill tone="info">Dream11 AI internship · working prototype</Pill>
          <h1>
            You looked away for twelve seconds.
            <br />
            The match did not.
          </h1>
          <p className="hero-lead">
            One tells you exactly what you missed — without spoiling the ball you paused on. The other fact-checks the
            creator you are listening to.
          </p>
          <div className="hero-actions">
            <button type="button" className="btn primary" onClick={() => go('gap')}>
              I missed a bit of the match →
            </button>
            <button type="button" className="btn ghost" onClick={() => go('creator')}>
              I'm listening to a creator →
            </button>
          </div>
          <div className="hero-facts">
            <span>No login</span>
            <span>Works with no API key</span>
            <span>Never shows you a ball past your own screen</span>
          </div>
        </div>
      </header>

      <div className="container">
        {/* ------------------------------------------------------- 3-step explainer */}
        <Section
          eyebrow="The whole idea in three steps"
          title="It does not guess. It rebuilds."
          lead="A chatbot asked “what did I miss?” invents a score. A highlight reel spoils the ball you were about to watch. This reads your own player's log, finds the exact balls you never saw, and answers with those only."
        >
          <div className="steps">
            <Step n={1} title="Your stream drops" tone="red">
              An ad, a freeze, or you join late. Your player logged every ball it actually showed you — and that log is
              the only thing the agent trusts.
            </Step>
            <Step n={2} title="It works out the gap" tone="amber">
              It finds the last ball you truly saw, then reads only what came after it. If two answers are possible, it
              asks you one short question instead of guessing.
            </Step>
            <Step n={3} title="You get a receipt, or an honest nothing" tone="teal">
              Runs, wickets, the asking rate — and a hard stop at the ball you paused on. If nothing key happened, it
              says so and charges you nothing.
            </Step>
          </div>
        </Section>

        {/* --------------------------------------------------------- product cards */}
        <Section
          eyebrow="Two artifacts, one engine"
          title="Pick your problem"
          lead="Both run on the same agent loop: the model proposes wording, code checks every number before it reaches your screen."
        >
          <div className="products">
            <article className="product">
              <Photo
                src="/img/phone-in-hand.jpg"
                alt="A hand holding a phone at night, screen glowing"
                ratio="16 / 10"
                credit={credit('phone-in-hand')}
              />
              <div className="product-body">
                <Pill tone="warn">For the fan who lost the stream</Pill>
                <h3>Gap Receipt</h3>
                <p>
                  “What did I miss?” answered with numbers you can check: the wickets, the boundaries, the asking rate
                  — and nothing from after the ball you paused on.
                </p>
                <ul className="ticks">
                  <li>Finds the gap from your player's own heartbeat log</li>
                  <li>Asks at most one question, and only when it changes the answer</li>
                  <li>Reads the scoreboard out of the photo's pixels</li>
                  <li>Honest “nothing happened” instead of filler</li>
                </ul>
                <button type="button" className="btn primary full" onClick={() => go('gap')}>
                  Open Gap Receipt →
                </button>
              </div>
            </article>

            <article className="product">
              <Photo
                src="/img/crowd-celebration.jpg"
                alt="A crowd of spectators standing and cheering at a cricket ground"
                ratio="16 / 10"
                credit={credit('crowd-celebration')}
              />
              <div className="product-body">
                <Pill tone="info">For the fan listening to a creator</Pill>
                <h3>Watch Along</h3>
                <p>
                  A Hinglish commentary stream turned into cards you can trust: the claim, whether the feed agrees, and
                  predictions settled on later balls.
                </p>
                <ul className="ticks">
                  <li>Transcribes the creator, then checks every spoken score against the feed</li>
                  <li>Flags the moment the creator is out of date — without hiding what he said</li>
                  <li>Logs his predictions and settles them on real balls</li>
                  <li>Text first: you never load the video just to get a score line</li>
                </ul>
                <button type="button" className="btn primary full" onClick={() => go('creator')}>
                  Open Watch Along →
                </button>
              </div>
            </article>
          </div>
        </Section>

        {/* -------------------------------------------------------------- demos */}
        <Section
          eyebrow="Try it — one click, nothing to install"
          title="Four situations, thirty seconds each"
          lead="One click opens a real agent run. You will see it think, ask you one question if it needs to, and publish a card."
        >
          <div className="demos">
            <DemoCard
              scenario="S1 · an ad"
              icon="📺"
              title="An ad covered the screen"
              what="You were watching ball 16.2 when a full-screen ad took over. A wicket fell while you were gone."
              agentDoes="Detects the ad, decides the last ball it can trust is uncertain, asks you one question, then commits a receipt for exactly the balls you missed."
              delay={0}
              onRun={() => launch('gap', 'S1')}
            />
            <DemoCard
              scenario="S2 · a stall"
              icon="⏳"
              title="The stream froze"
              what="A twelve-second buffer stall covered two balls while you were watching. Neither was a wicket or a boundary."
              agentDoes="Works out the gap, sees nothing worth your attention, and commits a deliberate no-op instead of inventing filler or selling you a clip."
              delay={70}
              onRun={() => launch('gap', 'S2')}
            />
            <DemoCard
              scenario="S3 · late start"
              icon="🚪"
              title="You joined late"
              what="You opened the app at 17.4 with no playback history at all, so nothing in the log can tell the agent where you came in."
              agentDoes="Says so plainly, then asks you to scope the recap: everything, key moments only, or straight to live."
              delay={140}
              onRun={() => launch('gap', 'S3')}
            />
            <DemoCard
              scenario="A · the creator"
              icon="🎙️"
              title="A creator stream, fact-checked"
              what="Twelve Hinglish segments from a live stream: tactical takes, a stale score, and one prediction about the next over."
              agentDoes="Drops the greetings with no model call, verifies the score claims against the feed, labels opinion as opinion, and settles the prediction on the ball that decides it."
              delay={210}
              onRun={() => launch('creator', 'ALL')}
            />
          </div>

          {view.sid && recentCards.length ? (
            <div className="home-latest">
              <h3>Your latest run</h3>
              <CardList cards={recentCards} />
              <button type="button" className="btn ghost" onClick={() => go(view.request?.pack === 'creator' ? 'creator' : 'gap')}>
                See the full run →
              </button>
            </div>
          ) : null}
        </Section>

        {/* ------------------------------------------------------------ the match */}
        <Section
          eyebrow="The match behind every demo"
          title="18 balls of a fictional chase"
          lead={`All four demos replay one short fictional match: ${facts.match.title}. It is a fixture, not real IPL or Dream11 data.`}
        >
          <Reveal>
            <MatchBar match={facts.match} state={facts.state} />
          </Reveal>
          <Reveal delay={80}>
            <div className="strip-wrap">
              <div className="strip-head">
                <h4>
                  The {facts.state.balls_in_fixture} balls the agent is allowed to see ({facts.state.window})
                </h4>
                <div className="legend">
                  <span className="lg dot">dot ball</span>
                  <span className="lg runs">runs</span>
                  <span className="lg four">four</span>
                  <span className="lg wicket">wicket</span>
                </div>
              </div>
              <BallStrip feed={feed} />
              <p className="tiny dim">
                {facts.state.boundaries} boundaries and {facts.state.wickets} wickets in the window; the chase ends at{' '}
                {facts.state.score} after {facts.state.last_over}.
              </p>
            </div>
          </Reveal>

          <Reveal delay={120}>
            <Drawer label="Show me every ball, in words" hint={`${feed.length} rows straight out of the fixture feed`}>
              <div className="table-wrap">
                <table className="feed-table">
                  <thead>
                    <tr>
                      <th>Ball</th>
                      <th>Runs</th>
                      <th>Score</th>
                      <th>What happened</th>
                      <th>Asking rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feed.map((e) => (
                      <tr key={e.id} className={e.wicket ? 'row-wicket' : e.runs >= 4 ? 'row-four' : ''}>
                        <td>{e.over}</td>
                        <td>{e.wicket ? 'W' : e.runs}</td>
                        <td>{e.score}</td>
                        <td>{e.text}</td>
                        <td>{e.rrr !== null ? e.rrr.toFixed(2) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Drawer>
          </Reveal>
        </Section>

        {/* ------------------------------------------------------------- frames */}
        <Section
          eyebrow="How it reads a scoreboard"
          title="Five photos, read digit by digit"
          lead="Rendered scoreboard frames. The agent matches each digit against a template of the same font, so the number on your receipt comes out of the pixels. One frame shows a score the feed never held — a real reader has to notice."
        >
          <FramesGallery frames={facts.frames} />
        </Section>

        {/* ------------------------------------------------------------ honesty */}
        <Section
          eyebrow="What is real here, and what is not"
          title="The honest version of the brochure"
          lead="Every claim on this page is checkable in one click, so the simulated parts sit next to the real ones."
        >
          <div className="provenance">
            {facts.provenance.map((p, i) => (
              <Reveal key={p.surface} delay={i * 50} className={`prov ${/real|live|recorded/i.test(p.state) && !/simulated/i.test(p.state) ? 'prov-real' : 'prov-sim'}`}>
                <div className="prov-head">
                  <b>{p.surface}</b>
                  <Pill tone={/absent/i.test(p.state) ? 'bad' : /simulated|text-to-speech|stand-in/i.test(p.state) ? 'warn' : 'good'}>
                    {p.state}
                  </Pill>
                </div>
                <p>{p.detail}</p>
              </Reveal>
            ))}
          </div>
        </Section>

        {/* ----------------------------------------------------------- glossary */}
        <Section eyebrow="Said plainly" title="The words this product uses" lead="If a term is not here, it is not on your screen.">
          <div className="glossary">
            <div>
              <b>Wicket</b>
              <span>A batter is out — the biggest thing that can happen while you are away.</span>
            </div>
            <div>
              <b>Asking rate</b>
              <span>Runs per over the chasing side still needs. It rises when they fall behind.</span>
            </div>
            <div>
              <b>No-op</b>
              <span>It looked, found nothing you needed, and deliberately said nothing.</span>
            </div>
            <div>
              <b>Your playback cursor</b>
              <span>The ball your own player had reached. Nothing after it is ever shown to you.</span>
            </div>
            <div>
              <b>Feed</b>
              <span>The official ball-by-ball record — the only thing allowed to certify a number.</span>
            </div>
            <div>
              <b>Recorded run</b>
              <span>A real model conversation saved and replayed, so the demo works with no key and no network.</span>
            </div>
          </div>
        </Section>

        {/* ------------------------------------------------------------- access */}
        <Section eyebrow="Run it yourself" title="Where this lives" tight>
          <div className="access">
            <div>
              <h4>This page is the artifact</h4>
              <p>One service serves this page and its API, so the only link you need is this one — no login, no key.</p>
              <div className="chips">
                <Pill tone="info">run mode: {manifest?.run_mode || facts.run.run_mode}</Pill>
                <Pill>model backend: {manifest?.backend || facts.run.llm_backend}</Pill>
                <Pill>{manifest?.cassettes ?? facts.run.cassettes} recorded model turns</Pill>
              </div>
            </div>
            <div>
              <h4>Reading mode matters</h4>
              <p>
                <b>Watch</b> lets you tap a clip and pay for it. <b>Quiet</b> refuses every video byte on the server and
                records what it refused. The toggle is real server behaviour, not a CSS class.
              </p>
              <button type="button" className="btn ghost" onClick={() => go('gap')}>
                Try the toggle on a gap scenario →
              </button>
            </div>
          </div>
        </Section>

        <footer className="foot">
          <div>
            <b>Photographs</b>
            <p>Licensed Wikimedia Commons images, used to set the scene. They are not evidence about this match.</p>
            <ul className="credits">
              {facts.photo_credits.map((c) => (
                <li key={c.file}>
                  <a href={c.source} target="_blank" rel="noreferrer noopener">
                    {c.title}
                  </a>{' '}
                  — {c.artist}, {c.license}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <b>Built for</b>
            <p>Dream11 AI internship assignment: one AI-powered artifact for a real fan problem. Two packs, one kernel.</p>
            <p className="tiny dim">
              Match, telemetry and audio are fixtures. The agent loop, verifier, byte meter and scoreboard reader are real
              code, running now.
            </p>
          </div>
        </footer>
      </div>
    </div>
  )
}

export { Empty }
