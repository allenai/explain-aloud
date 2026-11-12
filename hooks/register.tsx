import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

type Status = { phase: '' | 'generating' | 'speaking'; text: string }

const isPaused = atom(
  { plugin: 'explain-aloud', key: 'isPaused' } as const,
  false,
)

// What the pipeline is doing now; drives which buttons show and the label
// beside them: "🧠 writing script… 12s" (elapsed — generation can't report a
// true percentage), "🔊 60%" during playback (chars spoken / total).
const status = atom(
  { plugin: 'explain-aloud', key: 'status' } as const,
  { phase: '', text: '' } as Status,
)

// Locates this plugin's explain.py (newest installed copy in the plugin
// cache, or a checkout opened as the working directory) and the temp dir
// that holds its status file. Prints two lines: script path, temp dir.
const FIND_PATHS = `
import glob, os, tempfile
hits = glob.glob(os.path.expanduser('~/.claude/plugins/cache/*/explain-aloud/*/scripts/explain.py'))
local = os.path.join(os.getcwd(), 'scripts', 'explain.py')
if os.path.exists(local):
    hits.append(local)
hits.sort(key=os.path.getmtime)
print(hits[-1] if hits else '')
print(tempfile.gettempdir())
`

export const register: Register = on => {
  let cwd = ''
  let script = ''
  let statusPath = ''

  on('session.start', async ($, e, next) => {
    cwd = e.cwd
    const found = await $.process.run(['python3', '-c', FIND_PATHS])
    const [scriptLine, tmpLine] = found.stdout.split('\n')
    script = (scriptLine || '').trim()
    statusPath = `${(tmpLine || '/tmp').trim()}/claude-explain-aloud-status.json`

    $.clock.every(1500, async () => {
      let next_: Status = { phase: '', text: '' }
      try {
        if (await $.fs.exists(statusPath)) {
          const raw = await $.fs.read(statusPath)
          const s = JSON.parse(typeof raw === 'string' ? raw : '')
          if (s.phase === 'generating') {
            const secs = Math.max(0, Math.round(Date.now() / 1000 - s.started))
            next_ = { phase: 'generating', text: `🧠 writing script… ${secs}s` }
          } else if (s.phase === 'speaking' && s.total > 0) {
            const pct = Math.round((100 * s.done) / s.total)
            next_ = { phase: 'speaking', text: `🔊 ${pct}%` }
          }
        }
      } catch {
        next_ = { phase: '', text: '' }
      }
      const current = await read($, status)
      if (current.phase !== next_.phase || current.text !== next_.text) {
        await update($, status, () => next_)
        if (next_.phase === '') {
          await update($, isPaused, () => false)
        }
      }
    })

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !script) {
      return next(e)
    }

    const paused = await read($, isPaused)
    const st = await read($, status)
    const { Box, Button, Text } = $.ui.resolve(e)
    const run = (args: string[]) =>
      $.process.run(['python3', script, ...args])

    // Button has no disabled state and always inverts under the pointer, so an
    // inapplicable control is drawn as dim Text (no hover, nothing to press),
    // each inside a fixed-minWidth slot so the row never changes shape.
    const canExplain = st.phase === ''
    const canControl = st.phase === 'speaking'
    const canStop = st.phase !== ''

    const slot = (
      width: number,
      enabled: boolean,
      label: string,
      key: string,
      onPress: () => unknown,
    ) => (
      <Box minWidth={width}>
        {enabled
          ? <Button key={key} label={label} onPress={onPress} />
          : <Text dimColor>{label}</Text>}
      </Box>
    )

    return (
      <Box>
        <Text dimColor>explain-aloud </Text>
        {slot(13, canExplain, '🎙️ Explain', 'explain', async () => {
          await update($, isPaused, () => false)
          await update($, status, () =>
            ({ phase: 'generating', text: '🧠 writing script…' }) as Status)
          // Session id read at press time (a /clear changes it mid-process):
          // it pins the worker to THIS session's transcript — "newest in the
          // project" is another session's whenever two run side by side.
          const sid = await $.session.id()
          await run(['--worker', '--force', '--session-id', sid,
                     '--project', cwd, '--detach'])
        })}
        {slot(8, canControl, '⏪ 5s', 'back', () => run(['--back', '5']))}
        {slot(11, canControl, paused ? '▶ Resume' : '⏸ Pause', 'pause', async () => {
          const wantPause = !paused
          await run([wantPause ? '--pause' : '--resume'])
          await update($, isPaused, () => wantPause)
        })}
        {slot(9, canStop, '⏹ Stop', 'stop', async () => {
          await run(['--cancel'])
          await update($, isPaused, () => false)
          await update($, status, () => ({ phase: '', text: '' }) as Status)
        })}
        <Text dimColor> {st.text}</Text>
      </Box>
    )
  })
}
