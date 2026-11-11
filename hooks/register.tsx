import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

const isPaused = atom(
  { plugin: 'explain-aloud', key: 'isPaused' } as const,
  false,
)

// One line describing what the pipeline is doing right now, '' when idle:
// "🧠 writing script… 12s" while the model works (no true percentage exists
// there), "🔊 speaking 60%" during playback (chars spoken / total).
const statusText = atom(
  { plugin: 'explain-aloud', key: 'statusText' } as const,
  '',
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
      let text = ''
      try {
        if (await $.fs.exists(statusPath)) {
          const raw = await $.fs.read(statusPath)
          const s = JSON.parse(typeof raw === 'string' ? raw : '')
          if (s.phase === 'generating') {
            const secs = Math.max(0, Math.round(Date.now() / 1000 - s.started))
            text = `🧠 writing script… ${secs}s`
          } else if (s.phase === 'speaking' && s.total > 0) {
            text = `🔊 speaking ${Math.round((100 * s.done) / s.total)}%`
          }
        }
      } catch {
        text = ''
      }
      const current = await read($, statusText)
      if (current !== text) {
        await update($, statusText, () => text)
      }
    })

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || !script) {
      return next(e)
    }

    const paused = await read($, isPaused)
    const progress = await read($, statusText)
    const { Box, Button, Text } = $.ui.resolve(e)
    const run = (args: string[]) =>
      $.process.run(['python3', script, ...args])

    return (
      <Box>
        <Text dimColor>{progress ? `${progress} ` : 'explain-aloud '}</Text>
        <Button
          key="explain"
          label="🎙️ Explain"
          onPress={async () => {
            $.ui.toast('Generating spoken explanation (~15–40s)…')
            await update($, isPaused, () => false)
            await run(['--worker', '--force', '--project', cwd, '--detach'])
          }}
        />
        <Button
          key="back"
          label="⏪ 5s"
          onPress={() => run(['--back', '5'])}
        />
        <Button
          key="pause"
          label={paused ? '▶ Resume' : '⏸ Pause'}
          onPress={async () => {
            const wantPause = !paused
            await run([wantPause ? '--pause' : '--resume'])
            await update($, isPaused, () => wantPause)
          }}
        />
        <Button
          key="stop"
          label="⏹ Stop"
          onPress={async () => {
            await run(['--cancel'])
            await update($, isPaused, () => false)
          }}
        />
      </Box>
    )
  })
}
